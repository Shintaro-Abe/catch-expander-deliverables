// PoC品質: このコードはSpring AI + Spring Boot設定パターンのデモンストレーション用です。
// 本番利用にはSecrets Manager統合・本番グレードのVectorStoreチューニングが必要です。
package com.example.ai.config;

import org.springframework.ai.anthropic.AnthropicChatModel;
import org.springframework.ai.anthropic.AnthropicChatOptions;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.client.advisor.SimpleLoggerAdvisor;
import org.springframework.ai.chat.memory.ChatMemory;
import org.springframework.ai.chat.memory.InMemoryChatMemory;
import org.springframework.ai.embedding.BatchingStrategy;
import org.springframework.ai.embedding.TokenCountBatchingStrategy;
import org.springframework.ai.openai.api.common.OpenAiApiConstants;
import org.springframework.ai.tokenizer.JTokkitTokenCountEstimator;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

/**
 * Spring AI 設定クラス
 *
 * Spring Bootの@Configurationは、DIコンテナに登録するBeanを@Beanメソッドで定義する。
 * @ConditionalOnMissingBeanにより、ユーザー定義Beanが優先され、Auto-configurationが上書きされる。
 *
 * Beanのライフサイクル:
 * 1. インスタンス化 → 2. DIインジェクション → 3. @PostConstruct → 4. 使用 → 5. @PreDestroy
 *
 * 設定ファイル（application.yml）との役割分担:
 * - application.yml: APIキー・URL・モデル名等の外部化パラメータ
 * - @Configuration: 複数Beanの組み合わせ・条件分岐が必要な設定
 */
@Configuration
public class AiConfig {

    // ================================================================
    // ChatClient設定
    // ================================================================

    /**
     * ChatClientビーン（デフォルトプロファイル: Claude使用）
     *
     * ChatClientはSpring AIの統一的なLLMアクセスインターフェース。
     * WebClient / RestClientと同様のfluentなAPIを提供する。
     *
     * デフォルトAdvisors（毎回のリクエストに自動適用）:
     * - SimpleLoggerAdvisor: リクエスト/レスポンスのデバッグログ出力
     *
     * ※ RAGアドバイザーは用途によってリクエスト時に動的に追加する（RagService参照）
     *
     * @param model AnthropicChatModel（Auto-configurationで生成、application.ymlで設定）
     * @return 設定済みChatClient
     */
    @Bean
    @Profile("!ollama")  // ollamaプロファイル以外でClaude使用
    public ChatClient claudeChatClient(AnthropicChatModel model) {
        return ChatClient.builder(model)
                .defaultSystem("""
                        あなたは親切で知識豊富なAIアシスタントです。
                        日本語で回答し、専門用語には補足説明を付けてください。
                        根拠のある情報のみを提供し、不明な場合は正直に伝えてください。
                        """)
                .defaultOptions(AnthropicChatOptions.builder()
                        .model("claude-sonnet-4-20250514")
                        .maxTokens(4096)
                        .temperature(0.7)
                        .build())
                .defaultAdvisors(
                        new SimpleLoggerAdvisor()  // デバッグ用: 本番では無効化推奨
                )
                .build();
    }

    /**
     * ChatClientビーン（Ollamaプロファイル: ローカルLLM使用）
     *
     * ローカルLLM活用のメリット:
     * - APIコストゼロ（開発・テスト環境に最適）
     * - データがローカルに留まる（機密情報の処理に安全）
     * - ネットワーク不要の環境でも動作
     *
     * 起動方法: `ollama run llama3.2` (事前にOllamaインストール必要)
     */
    @Bean
    @Profile("ollama")
    public ChatClient ollamaChatClient(
            org.springframework.ai.ollama.OllamaChatModel model) {
        return ChatClient.builder(model)
                .defaultSystem("あなたは親切なAIアシスタントです。日本語で回答してください。")
                .defaultAdvisors(new SimpleLoggerAdvisor())
                .build();
    }

    // ================================================================
    // 会話メモリ設定
    // ================================================================

    /**
     * 会話メモリビーン（インメモリ実装）
     *
     * InMemoryChatMemory: サーバー再起動で消去される揮発性メモリ
     *
     * 本番環境での代替実装:
     * - CassandraChatMemory: 高可用性・大規模向け（Cassandraが必要）
     * - JdbcChatMemory: 既存RDBMSを流用（spring-ai-jdbc-chat-memory）
     * - RedisChatMemory: 高速・TTL設定可能（Redisが必要）
     *
     * @return InMemoryChatMemoryインスタンス
     */
    @Bean
    public ChatMemory chatMemory() {
        return new InMemoryChatMemory();
    }

    // ================================================================
    // Embeddingバッチ処理最適化
    // ================================================================

    /**
     * トークン数ベースのバッチング戦略
     *
     * 大量ドキュメントのEmbedding処理時にトークン上限エラーを防止する。
     * デフォルト実装より安全なバッチサイズ制御が可能。
     *
     * パラメータ:
     * - maxTokensPerBatch: 8,000トークン/バッチ（OpenAI上限: 8,192）
     * - bufferRatio: 0.1（10%バッファを確保してエラーを防止）
     *
     * @return カスタムバッチング戦略
     */
    @Bean
    public BatchingStrategy tokenCountBatchingStrategy() {
        return new TokenCountBatchingStrategy(
                new JTokkitTokenCountEstimator(),
                8000,  // トークン上限（モデルの最大値より少し低めに設定）
                0.1    // 10%安全バッファ
        );
    }
}
