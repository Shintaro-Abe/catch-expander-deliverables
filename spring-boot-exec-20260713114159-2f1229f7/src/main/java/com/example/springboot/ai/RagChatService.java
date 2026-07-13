// PoC 品質 — 実運用前にセキュリティ・業務ロジックを追加すること

package com.example.springboot.ai;

import lombok.extern.slf4j.Slf4j;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.client.advisor.MessageChatMemoryAdvisor;
import org.springframework.ai.chat.client.advisor.RetrievalAugmentationAdvisor;
import org.springframework.ai.chat.client.advisor.SimpleLoggerAdvisor;
import org.springframework.ai.chat.memory.ChatMemory;
import org.springframework.ai.chat.memory.InMemoryChatMemory;
import org.springframework.ai.document.Document;
import org.springframework.ai.rag.retrieval.search.VectorStoreDocumentRetriever;
import org.springframework.ai.rag.query.transformer.RewriteQueryTransformer;
import org.springframework.ai.vectorstore.SearchRequest;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;

import java.util.List;

/**
 * Spring AI 1.0+ RAG（Retrieval-Augmented Generation）実装例
 *
 * 設計ポイント:
 *  - ChatClient（Spring AI 中心抽象層）でプロバイダー非依存の呼び出し
 *  - Advisors API でRAG・会話記憶・ロギングをチェーン構成
 *  - Modular RAG: QueryTransformer → VectorStoreRetriever → ContextAugmenter
 *  - ストリーミング応答（Flux<String>）でUXを向上
 *
 * 必要な依存関係（pom.xml）:
 *   spring-boot-starter-web
 *   spring-ai-openai-spring-boot-starter（または anthropic 等）
 *   spring-ai-pgvector-store-spring-boot-starter（VectorStore 実装）
 *
 * 必要なプロパティ（application.yml 参照）:
 *   spring.ai.openai.api-key または spring.ai.anthropic.api-key
 *   spring.ai.vectorstore.pgvector.*
 */
@Slf4j
@Service
public class RagChatService {

    private final ChatClient chatClient;
    private final VectorStore vectorStore;

    // ─── コンストラクタ注入（推奨パターン）──────────────────────────────────────
    //   フィールド注入（@Autowired）はテスト困難なため使用しない
    public RagChatService(ChatClient.Builder chatClientBuilder, VectorStore vectorStore) {
        this.vectorStore = vectorStore;

        // デフォルト Advisor を設定して ChatClient を構築
        ChatMemory memory = new InMemoryChatMemory(); // 本番: JdbcChatMemory 等を使用

        this.chatClient = chatClientBuilder
                .defaultSystem("""
                        あなたは社内ナレッジベースを活用するアシスタントです。
                        提供されたコンテキスト情報を基に、正確・簡潔に回答してください。
                        コンテキストにない情報については、その旨を明示してください。
                        """)
                .defaultAdvisors(
                        // 会話履歴を自動管理（直近10メッセージ）
                        new MessageChatMemoryAdvisor(memory),
                        // リクエスト/レスポンスをデバッグログ出力
                        new SimpleLoggerAdvisor()
                )
                .build();
    }

    // ─── 1. シンプルチャット（RAGなし）────────────────────────────────────────

    /**
     * 同期チャット（会話記憶あり）
     *
     * @param conversationId セッション識別子（会話履歴の分離に使用）
     * @param userMessage    ユーザー入力
     * @return LLM の応答テキスト
     */
    public String chat(String conversationId, String userMessage) {
        return chatClient.prompt()
                .user(userMessage)
                // 会話履歴をセッションIDで分離
                .advisors(a -> a.param(MessageChatMemoryAdvisor.CHAT_MEMORY_CONVERSATION_ID_KEY,
                        conversationId))
                .call()
                .content();
    }

    /**
     * ストリーミングチャット（会話記憶あり）
     * Flux<String> を SSE（Server-Sent Events）などで配信することでUXを向上
     *
     * @param conversationId セッション識別子
     * @param userMessage    ユーザー入力
     * @return トークン単位の文字列ストリーム
     */
    public Flux<String> chatStream(String conversationId, String userMessage) {
        return chatClient.prompt()
                .user(userMessage)
                .advisors(a -> a.param(MessageChatMemoryAdvisor.CHAT_MEMORY_CONVERSATION_ID_KEY,
                        conversationId))
                .stream()
                .content();
    }

    // ─── 2. Naive RAG（シンプルな類似検索）────────────────────────────────────

    /**
     * Naive RAG チャット
     * VectorStore から類似文書を取得し、システムプロンプトへ挿入する最もシンプルな実装
     *
     * @param question ユーザーの質問
     * @return コンテキストを参照した応答
     */
    public String chatWithNaiveRag(String question) {
        // 類似検索（topK=5、類似度閾値0.7）
        List<Document> contexts = vectorStore.similaritySearch(
                SearchRequest.builder()
                        .query(question)
                        .topK(5)
                        .similarityThreshold(0.7)
                        .build()
        );

        // 取得コンテキストをテキスト結合してシステムプロンプトへ付加
        String contextText = contexts.stream()
                .map(Document::getText)
                .reduce("", (a, b) -> a + "\n\n" + b);

        return chatClient.prompt()
                .system(sp -> sp.param("context", contextText))
                .user(question)
                .call()
                .content();
    }

    // ─── 3. Modular RAG（高度なパイプライン）──────────────────────────────────

    /**
     * Modular RAG チャット（Spring AI 推奨パターン）
     *
     * パイプライン構成:
     *   [Pre-Retrieval] RewriteQueryTransformer: 曖昧なクエリを書き換え
     *       ↓
     *   [Retrieval] VectorStoreDocumentRetriever: 類似文書を取得
     *       ↓
     *   [Generation] ContextualQueryAugmenter: コンテキストをプロンプトへ組み込み
     *
     * @param conversationId セッション識別子
     * @param question       ユーザーの質問（曖昧なクエリも可）
     * @return コンテキスト拡張された応答
     */
    public String chatWithModularRag(String conversationId, String question) {

        // RetrievalAugmentationAdvisor: Modular RAG の標準 Advisor
        RetrievalAugmentationAdvisor ragAdvisor = RetrievalAugmentationAdvisor.builder()
                // クエリ書き換え（LLMで曖昧表現を解消）
                .queryTransformers(
                        RewriteQueryTransformer.builder()
                                .chatClientBuilder(chatClient.mutate())
                                .build()
                )
                // ベクターストア検索
                .documentRetriever(
                        VectorStoreDocumentRetriever.builder()
                                .vectorStore(vectorStore)
                                .topK(5)
                                .similarityThreshold(0.6)
                                // メタデータフィルター例: 特定カテゴリのドキュメントのみ取得
                                // .filterExpression("category == 'technical'")
                                .build()
                )
                .build();

        return chatClient.prompt()
                .user(question)
                .advisors(ragAdvisor)
                .advisors(a -> a.param(MessageChatMemoryAdvisor.CHAT_MEMORY_CONVERSATION_ID_KEY,
                        conversationId))
                .call()
                .content();
    }

    // ─── 4. ドキュメント取り込み（インデクシング）──────────────────────────────

    /**
     * ドキュメントをVectorStoreに追加（RAG用インデックス構築）
     *
     * 実際の使用例:
     *   - PDF / Markdown / Webページを読み込み → チャンク分割 → 埋め込み生成 → 保存
     *   - ETLパイプライン: TextReader → TokenTextSplitter → VectorStore.add()
     *
     * @param texts   インデックスするテキストのリスト
     * @param source  メタデータ: ドキュメントのソース識別子
     */
    public void indexDocuments(List<String> texts, String source) {
        List<Document> documents = texts.stream()
                .map(text -> new Document(text,
                        java.util.Map.of("source", source, "indexedAt",
                                java.time.Instant.now().toString())))
                .toList();

        // add() 内で EmbeddingModel が自動的に埋め込みベクトルを生成・保存
        vectorStore.add(documents);
        log.info("Indexed {} documents from source: {}", documents.size(), source);
    }

    // ─── 5. 構造化出力（Structured Output）────────────────────────────────────

    /**
     * LLM の応答を型安全なオブジェクトとして取得
     * Pydantic のような自動バリデーション・デシリアライズ
     *
     * @param userMessage 質問文
     * @return 構造化されたレスポンスオブジェクト
     */
    public SummaryResponse summarizeToStructured(String userMessage) {
        return chatClient.prompt()
                .user(userMessage)
                .call()
                .entity(SummaryResponse.class);
    }

    // ─── レスポンスDTO ─────────────────────────────────────────────────────────

    /**
     * 構造化出力用レコード
     * LLM に対して JSON スキーマを自動生成・注入し、型安全な応答を実現
     */
    public record SummaryResponse(
            String summary,
            List<String> keyPoints,
            String category,
            int confidenceScore
    ) {}
}
