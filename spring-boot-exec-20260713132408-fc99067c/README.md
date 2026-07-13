## プログラムコード（Python またはユーザープロファイルの技術スタック）

# Spring Boot + Spring AI RAG デモアプリ

> **PoC品質**: 本リポジトリは学習・デモ目的のコードです。本番利用には認証・セキュリティ強化が必要です。

Spring Boot 3.x + Spring AI 1.x を使った **RAG（Retrieval-Augmented Generation）** パイプラインのサンプル実装です。Claude / Ollama（ローカルLLM）の両方に対応しています。

---

## アーキテクチャ概要

```
[クライアント]
     │
     ▼ REST API（/api/v1/chat, /api/v1/chat/rag）
[Spring Boot Controller]
     │
     ▼ Service層
[RagService]
  ├── ChatClient（Claude / Ollama）
  ├── QuestionAnswerAdvisor ──── [PgVector] ← Embedding検索
  ├── MessageChatMemoryAdvisor ─ [InMemoryChatMemory]
  └── TokenTextSplitter ──────── [PgVector] ← ドキュメント保存
```

### レイヤードアーキテクチャの役割分担

| 層 | クラス | 責務 |
|---|---|---|
| **Controller** | `ChatController` | HTTPリクエスト処理・バリデーションのみ |
| **Service** | `RagService` | RAGパイプライン・会話メモリ・ビジネスロジック |
| **Config** | `AiConfig` | ChatClient・ChatMemory・BatchingStrategyのBean定義 |

---

## Spring AI RAG コンポーネント解説

### QuestionAnswerAdvisor（シンプルRAG）

```java
var advisor = QuestionAnswerAdvisor.builder(vectorStore)
    .searchRequest(SearchRequest.builder()
        .similarityThreshold(0.7)  // コサイン類似度の閾値
        .topK(5)                   // 取得件数
        .build())
    .build();
```

- **用途**: プロトタイプ・シンプルなユースケース
- **特徴**: ゼロコンフィグ、単一VectorStoreとの統合

### RetrievalAugmentationAdvisor（モジュラーRAG）

```java
var advisor = RetrievalAugmentationAdvisor.builder()
    .documentRetriever(VectorStoreDocumentRetriever.builder()
        .vectorStore(vectorStore)
        .filterExpression(filter)
        .build())
    .build();
```

- **用途**: 本番環境・マルチテナント・高度なフィルタリング
- **特徴**: クエリ書き換え・翻訳・展開など前処理が構成可能

### QuestionAnswerAdvisor vs RetrievalAugmentationAdvisor

| 観点 | QuestionAnswerAdvisor | RetrievalAugmentationAdvisor |
|---|---|---|
| **設定コスト** | ゼロコンフィグ | 要設定 |
| **クエリ前処理** | なし | 書き換え・翻訳・展開が可能 |
| **マルチストア** | 単一のみ | 複数ストア結合可 |
| **推奨用途** | プロトタイプ | 本番 |

---

## セットアップ

### 前提条件

| 項目 | バージョン |
|---|---|
| Java | 21+ |
| Spring Boot | 3.3+ |
| Spring AI | 1.0+ |
| PostgreSQL + pgvector | 16+ + 0.7+ |

### 1. PostgreSQL + pgvector起動（Docker Compose）

```yaml
# docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: aidb
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: secret
    ports:
      - "5432:5432"
```

```bash
docker compose up -d
```

### 2. 環境変数の設定

```bash
# .env（リポジトリにコミットしないこと）
export ANTHROPIC_API_KEY=sk-ant-xxxxxxxx

# Ollamaを使う場合（APIキー不要）
ollama pull llama3.2
```

### 3. application.yml設定

```yaml
spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/aidb
    username: postgres
    password: ${POSTGRES_PASSWORD:secret}

  ai:
    anthropic:
      api-key: ${ANTHROPIC_API_KEY}
      chat:
        model: claude-sonnet-4-20250514
        max-tokens: 4096

    vectorstore:
      pgvector:
        index-type: HNSW          # 近似最近傍探索インデックス（高速）
        distance-type: COSINE_DISTANCE
        dimensions: 1536          # text-embedding-3-smallの次元数
        initialize-schema: true   # 起動時にテーブル自動作成

    # Ollama使用時（ローカルLLM）
    ollama:
      base-url: http://localhost:11434
      chat:
        model: llama3.2
```

### 4. アプリケーション起動

```bash
# Claudeを使用（デフォルト）
./mvnw spring-boot:run

# Ollamaを使用（ローカルLLM）
./mvnw spring-boot:run -Dspring-boot.run.profiles=ollama
```

---

## API使用例

### ドキュメントの取り込み

```bash
curl -X POST http://localhost:8080/api/v1/documents \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Spring AIはSpring Boot 3.xのAI統合フレームワークです...",
    "metadata": {
      "source": "spring-ai-docs",
      "type": "documentation",
      "tenantId": "tenant-001"
    }
  }'
```

### RAGチャット

```bash
curl -X POST http://localhost:8080/api/v1/chat/rag \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Spring AIのVectorStoreには何の実装がありますか？",
    "conversationId": "user-session-123"
  }'
```

### SSEストリーミングチャット

```bash
curl -N "http://localhost:8080/api/v1/chat/stream?question=Spring+Bootとは何ですか&conversationId=session-456"
```

---

## Auto-configurationの仕組み

Spring Bootの `@SpringBootApplication` がトリガーとなり、以下が自動実行される：

```
@SpringBootApplication
├── @ComponentScan           → com.example.ai 以下のBeanを自動検出
├── @EnableAutoConfiguration → クラスパスのライブラリを元に自動設定
│   ├── spring-ai-starter-model-anthropic → AnthropicChatModel自動生成
│   ├── spring-ai-vectorstore-pgvector   → PgVectorStore自動生成
│   └── @ConditionalOnMissingBean        → ユーザー定義Beanが優先
└── @Configuration           → BeanメタデータをApplicationContextに登録
```

> **デバッグTips**: `application.properties` に `debug=true` を追加すると、
> どのAuto-configurationが適用/スキップされたかを **Conditions Evaluation Report** で確認できます。

---

## AWS ECS Fargateへのデプロイ概要

| 手順 | コマンド |
|---|---|
| Dockerイメージビルド | `docker build -t springai-demo .` |
| ECRへプッシュ | `docker push <account>.dkr.ecr.ap-northeast-1.amazonaws.com/springai-demo` |
| Secrets Manager統合 | `spring.ai.anthropic.api-key=${ANTHROPIC_API_KEY}` をECSタスクロールで解決 |
| SnapStart対応（Lambda） | タスク定義で `SnapStart: "ApplyOn": "PublishedVersions"` を設定 |

---

## 依存関係（pom.xml 抜粋）

```xml
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.5</version>
</parent>

<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>org.springframework.ai</groupId>
            <artifactId>spring-ai-bom</artifactId>
            <version>1.0.0</version>
            <type>pom</type>
            <scope>import</scope>
        </dependency>
    </dependencies>
</dependencyManagement>

<dependencies>
    <!-- Spring Boot Web -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <!-- Spring AI: Anthropic Claude -->
    <dependency>
        <groupId>org.springframework.ai</groupId>
        <artifactId>spring-ai-starter-model-anthropic</artifactId>
    </dependency>
    <!-- Spring AI: Ollama（ローカルLLM） -->
    <dependency>
        <groupId>org.springframework.ai</groupId>
        <artifactId>spring-ai-starter-model-ollama</artifactId>
    </dependency>
    <!-- Spring AI: PgVector VectorStore -->
    <dependency>
        <groupId>org.springframework.ai</groupId>
        <artifactId>spring-ai-starter-vector-store-pgvector</artifactId>
    </dependency>
    <!-- バリデーション -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-validation</artifactId>
    </dependency>
    <!-- Lombok（ボイラープレート削減） -->
    <dependency>
        <groupId>org.projectlombok</groupId>
        <artifactId>lombok</artifactId>
        <optional>true</optional>
    </dependency>
</dependencies>
```


---

📝 [Notionで詳細を見る]()
