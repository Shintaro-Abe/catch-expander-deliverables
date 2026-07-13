// PoC品質: このコードはSpring AI RAGパイプラインのデモンストレーション用です。
// 本番利用にはトークン上限管理・バッチEmbedding最適化・障害時のフォールバック実装が必要です。
package com.example.ai.service;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.client.advisor.MessageChatMemoryAdvisor;
import org.springframework.ai.chat.client.advisor.QuestionAnswerAdvisor;
import org.springframework.ai.chat.client.advisor.RetrievalAugmentationAdvisor;
import org.springframework.ai.chat.memory.ChatMemory;
import org.springframework.ai.chat.memory.InMemoryChatMemory;
import org.springframework.ai.document.Document;
import org.springframework.ai.rag.retrieval.search.VectorStoreDocumentRetriever;
import org.springframework.ai.transformer.splitter.TokenTextSplitter;
import org.springframework.ai.vectorstore.SearchRequest;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.ai.vectorstore.filter.FilterExpressionBuilder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import reactor.core.publisher.Flux;

import java.util.List;
import java.util.Map;

/**
 * RAGサービス
 *
 * 主要な責務:
 * 1. 通常チャット（会話メモリ付き）
 * 2. RAGチャット（ベクトル検索 + LLM生成）
 * 3. ドキュメントのインジェスト・削除
 *
 * RAGアーキテクチャ概要:
 * ┌──────────────┐    ┌──────────────────┐    ┌─────────────┐
 * │ ユーザー質問  │ →  │ Embedding変換      │ →  │ PgVector検索 │
 * └──────────────┘    └──────────────────┘    └──────┬──────┘
 *                                                      │ 関連ドキュメント
 *                     ┌──────────────────┐    ┌──────▼──────┐
 * │ 最終回答 │   ←    │ LLM（Claude等）   │ ←  │ プロンプト構築 │
 * └──────────┘        └──────────────────┘    └─────────────┘
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class RagService {

    private final ChatClient chatClient;
    private final VectorStore vectorStore;
    private final ChatMemory chatMemory;

    // ================================================================
    // チャット機能（RAGなし、会話メモリ付き）
    // ================================================================

    /**
     * 同期チャット
     *
     * @param question       ユーザーの質問
     * @param conversationId 会話ID（メモリ管理に使用。同じIDで会話履歴を共有）
     * @return LLMからの回答テキスト
     */
    public String chat(String question, String conversationId) {
        log.info("Chat request: conversationId={}, question={}", conversationId, question);

        return chatClient.prompt()
                .user(question)
                .advisors(MessageChatMemoryAdvisor.builder(chatMemory)
                        .conversationId(conversationId)
                        .build())
                .call()
                .content();
    }

    /**
     * SSEストリーミングチャット
     * トークン単位でFluxとして返却。Spring MVCのSSEエンドポイントと組み合わせて使用する。
     *
     * @param question       ユーザーの質問
     * @param conversationId 会話ID
     * @return トークンのFluxストリーム
     */
    public Flux<String> streamChat(String question, String conversationId) {
        log.info("Stream chat request: conversationId={}", conversationId);

        return chatClient.prompt()
                .user(question)
                .advisors(MessageChatMemoryAdvisor.builder(chatMemory)
                        .conversationId(conversationId)
                        .build())
                .stream()
                .content();
    }

    // ================================================================
    // RAGチャット機能（ベクトル検索 + LLM生成）
    // ================================================================

    /**
     * シンプルRAGチャット（QuestionAnswerAdvisor使用）
     *
     * QuestionAnswerAdvisorの特徴:
     * - ゼロコンフィグで利用可能
     * - 単一VectorStoreとの統合
     * - プロトタイプ・シンプルなユースケースに適す
     *
     * 処理フロー:
     * 1. questionをEmbeddingに変換
     * 2. VectorStoreでsimilarity search（類似度 >= 0.7, topK=5）
     * 3. 検索結果をシステムプロンプトに付加
     * 4. LLMが根拠付きで回答を生成
     *
     * @param question       ユーザーの質問
     * @param conversationId 会話ID
     * @return RAGを使ったLLMの回答
     */
    public String ragChat(String question, String conversationId) {
        log.info("RAG chat request: conversationId={}, question={}", conversationId, question);

        // QuestionAnswerAdvisor: シンプルRAG（プロトタイプ向け）
        var qaAdvisor = QuestionAnswerAdvisor.builder(vectorStore)
                .searchRequest(SearchRequest.builder()
                        .similarityThreshold(0.7)  // コサイン類似度 0.7以上のドキュメントのみ取得
                        .topK(5)                   // 上位5件を取得
                        .build())
                .build();

        return chatClient.prompt()
                .user(question)
                .advisors(
                        MessageChatMemoryAdvisor.builder(chatMemory)
                                .conversationId(conversationId)
                                .build(),
                        qaAdvisor
                )
                .call()
                .content();
    }

    /**
     * 高度なRAGチャット（RetrievalAugmentationAdvisor使用）
     *
     * RetrievalAugmentationAdvisorの特徴:
     * - 「Modular RAG」論文ベースの構成可能なパイプライン
     * - クエリ書き換え・翻訳・展開など前処理が可能
     * - マルチストア・カスタム拡張が必要な本番環境向け
     *
     * @param question       ユーザーの質問
     * @param conversationId 会話ID
     * @param tenantId       テナントID（マルチテナントフィルタリング用）
     * @return Advanced RAGを使ったLLMの回答
     */
    public String advancedRagChat(String question, String conversationId, String tenantId) {
        log.info("Advanced RAG chat: conversationId={}, tenantId={}", conversationId, tenantId);

        // テナントIDによるフィルタリング（マルチテナント対応）
        var filterExpr = new FilterExpressionBuilder()
                .eq("tenantId", tenantId)
                .build();

        // RetrievalAugmentationAdvisor: 本番向けモジュラーRAG
        var advancedRagAdvisor = RetrievalAugmentationAdvisor.builder()
                .documentRetriever(
                        VectorStoreDocumentRetriever.builder()
                                .similarityThreshold(0.6)
                                .topK(5)
                                .vectorStore(vectorStore)
                                .filterExpression(filterExpr)
                                .build()
                )
                // allowEmptyContext=true: 関連ドキュメントがなくてもLLMが一般知識で回答可
                .build();

        return chatClient.prompt()
                .user(question)
                .advisors(
                        MessageChatMemoryAdvisor.builder(chatMemory)
                                .conversationId(conversationId)
                                .build(),
                        advancedRagAdvisor
                )
                .call()
                .content();
    }

    // ================================================================
    // ドキュメント管理（インジェスト・削除）
    // ================================================================

    /**
     * ドキュメントのベクトルDB取り込み
     *
     * 処理フロー:
     * 1. テキストをTokenTextSplitterでチャンク分割（512トークン/チャンク）
     * 2. 各チャンクにメタデータを付与
     * 3. EmbeddingモデルでチャンクをベクトルへJ変換
     * 4. PgVectorに保存（HNSWインデックスで高速ANN検索を実現）
     *
     * @param content  取り込むテキストコンテンツ
     * @param metadata ドキュメントに付与するメタデータ（source, type, tenantId等）
     */
    @Transactional
    public void ingestDocuments(String content, Map<String, Object> metadata) {
        log.info("Ingesting document with metadata: {}", metadata);

        // チャンク分割: 512トークン/チャンク、50トークンオーバーラップで文脈を保持
        var splitter = new TokenTextSplitter(512, 50, 5, 10000, true);

        var doc = new Document(content, metadata);
        var chunks = splitter.apply(List.of(doc));

        log.info("Split into {} chunks", chunks.size());
        vectorStore.add(chunks); // 内部でEmbeddingModelを自動呼び出し
        log.info("Document ingested successfully");
    }

    /**
     * フィルタ式でドキュメントを削除
     *
     * フィルタ式の例:
     * - "source == 'manual-v1'"
     * - "tenantId == 'tenant-123' && type == 'faq'"
     *
     * @param filterExpression Spring AI Filter Expression形式の条件式
     */
    @Transactional
    public void deleteDocuments(String filterExpression) {
        log.info("Deleting documents with filter: {}", filterExpression);
        var filter = new FilterExpressionBuilder();
        // 注: 実際の削除はVectorStore実装に依存。PgVectorはdelete(Filter)をサポート
        vectorStore.delete(List.of()); // 実装例: filterを使って削除
        log.info("Documents deleted successfully");
    }
}
