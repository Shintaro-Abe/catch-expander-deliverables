// PoC品質: このコードはSpring Boot REST API設計パターンのデモンストレーション用です。
// 本番利用には認証（Spring Security）・レートリミット・詳細なロギングが必要です。
package com.example.ai.controller;

import com.example.ai.dto.ChatRequest;
import com.example.ai.dto.ChatResponse;
import com.example.ai.dto.IngestRequest;
import com.example.ai.service.RagService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Flux;

/**
 * チャット・RAG APIコントローラ
 *
 * 設計方針:
 * - URLはリソース名詞を使用（RESTful設計）
 * - ControllerはHTTP処理のみ担当、ビジネスロジックはServiceに委譲
 * - @Valid でリクエストボディをバリデーション
 * - ストリーミングはFlux<String>で返却（WebFlux不要、Spring MVC SSE対応）
 *
 * エンドポイント一覧:
 * POST /api/v1/chat            - 同期チャット（Claude or Ollama）
 * POST /api/v1/chat/stream     - SSEストリーミングチャット
 * POST /api/v1/chat/rag        - RAG（検索拡張生成）チャット
 * POST /api/v1/documents       - ドキュメントのベクトルDB取り込み
 * DELETE /api/v1/documents     - ベクトルDBのドキュメント削除
 */
@RestController
@RequestMapping("/api/v1")
@Validated
@RequiredArgsConstructor
public class ChatController {

    private final RagService ragService;

    /**
     * 同期チャット
     * LLMに質問を送り、完全な回答を返す（シンプルなユースケース向け）
     */
    @PostMapping("/chat")
    public ResponseEntity<ChatResponse> chat(@Valid @RequestBody ChatRequest request) {
        String answer = ragService.chat(request.question(), request.conversationId());
        return ResponseEntity.ok(new ChatResponse(answer, request.conversationId()));
    }

    /**
     * SSEストリーミングチャット
     * LLMの回答をトークン単位でストリーミング返却（UX改善向け）
     *
     * クライアント側サンプル（JavaScript）:
     *   const es = new EventSource('/api/v1/chat/stream?question=...');
     *   es.onmessage = (e) => console.log(e.data);
     */
    @GetMapping(value = "/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> streamChat(
            @RequestParam @NotBlank String question,
            @RequestParam(required = false, defaultValue = "default") String conversationId) {
        return ragService.streamChat(question, conversationId);
    }

    /**
     * RAGチャット（検索拡張生成）
     * ベクトルDBから関連ドキュメントを検索し、コンテキストとしてLLMに提供して回答生成
     *
     * 処理フロー:
     * 1. クエリをEmbeddingモデルでベクトル化
     * 2. PgVectorで類似ドキュメントを検索（cosine similarity）
     * 3. 検索結果をシステムプロンプトに付加
     * 4. LLMが根拠付きで回答を生成
     */
    @PostMapping("/chat/rag")
    public ResponseEntity<ChatResponse> ragChat(@Valid @RequestBody ChatRequest request) {
        String answer = ragService.ragChat(request.question(), request.conversationId());
        return ResponseEntity.ok(new ChatResponse(answer, request.conversationId()));
    }

    /**
     * ドキュメント取り込み（インジェスト）
     * テキストをチャンク分割 → Embedding → PgVectorに保存
     */
    @PostMapping("/documents")
    public ResponseEntity<Void> ingestDocuments(@Valid @RequestBody IngestRequest request) {
        ragService.ingestDocuments(request.content(), request.metadata());
        return ResponseEntity.noContent().build();
    }

    /**
     * ドキュメント削除
     * メタデータフィルタで一致するドキュメントをベクトルDBから削除
     */
    @DeleteMapping("/documents")
    public ResponseEntity<Void> deleteDocuments(
            @RequestParam @NotBlank String filterExpression) {
        ragService.deleteDocuments(filterExpression);
        return ResponseEntity.noContent().build();
    }
}
