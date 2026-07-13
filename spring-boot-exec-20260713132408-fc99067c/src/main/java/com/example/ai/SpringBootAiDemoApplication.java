// PoC品質: このコードはSpring Boot + Spring AI RAGパイプラインのデモンストレーション用です。
// 本番利用には認証・エラーハンドリング・セキュリティ設定の強化が必要です。
package com.example.ai;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Spring Boot + Spring AI RAGデモアプリ
 *
 * 主要機能:
 * - ClaudeおよびOllamaを用いたLLMチャットAPI
 * - PgVectorを使ったRAGパイプライン（QuestionAnswerAdvisor / RetrievalAugmentationAdvisor）
 * - 会話メモリ（InMemoryChatMemory）統合
 * - グローバル例外ハンドリング + Bean Validation
 *
 * 前提条件:
 * - Java 21+（Virtual Threads対応）
 * - Spring Boot 3.3+
 * - Spring AI 1.0+ (spring-ai-starter-model-anthropic, spring-ai-starter-model-ollama)
 * - PostgreSQL + PgVector拡張（ローカルはDocker Composeで起動推奨）
 */
@SpringBootApplication
public class SpringBootAiDemoApplication {

    public static void main(String[] args) {
        SpringApplication.run(SpringBootAiDemoApplication.class, args);
    }
}
