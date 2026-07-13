## プログラムコード（Python またはユーザープロファイルの技術スタック）

# Spring Boot 実装リファレンス（PoC 品質）

> **注意**: このコードは学習・プロトタイピング目的の PoC（概念実証）です。実運用前に認証・認可・シークレット管理・テストを追加してください。

---

## 概要

Spring Boot の主要機能をカバーする実装サンプル集です。AIエンジニア視点で特に重要な **Spring AI + RAG 統合** を中心に、REST API 設計・例外ハンドリング・クラウド設定のベストプラクティスをまとめています。

---

## ファイル構成

```
src/
├── main/
│   ├── java/com/example/springboot/
│   │   ├── controller/
│   │   │   └── UserController.java         # REST API（CRUD・バリデーション・DTOパターン）
│   │   ├── exception/
│   │   │   └── GlobalExceptionHandler.java # グローバル例外ハンドリング
│   │   └── ai/
│   │       └── RagChatService.java         # Spring AI 1.0+ RAGパイプライン実装
│   └── resources/
│       └── application.yml                 # 設定（OpenAI / Anthropic / PgVector / Actuator）
└── README.md
```

---

## 各ファイルのポイント

### 1. `UserController.java` — REST API ベストプラクティス

| 機能 | 説明 |
|------|------|
| `@RestController` | `@Controller` + `@ResponseBody` の合成（JSON自動シリアライズ） |
| `@Valid @RequestBody` | Bean Validation を有効化（失敗時 `MethodArgumentNotValidException`） |
| `ResponseEntity<T>` | HTTPステータスを柔軟に制御（201 Created、204 No Content 等） |
| `ApiResponse<T>` | 統一レスポンスラッパーでエンドポイント間の一貫性を確保 |
| コメントアウト部分 | DTO・Service クラスの実装スケルトン（別ファイルに展開して使用） |

**HTTP ステータスの使い分け**:
```
GET  /api/v1/users      → 200 OK
GET  /api/v1/users/{id} → 200 OK / 404 Not Found
POST /api/v1/users      → 201 Created
PUT  /api/v1/users/{id} → 200 OK / 404 Not Found
DELETE /api/v1/users/{id}→ 204 No Content / 404 Not Found
```

---

### 2. `GlobalExceptionHandler.java` — 例外の一元管理

```
例外優先度（高 → 低）:
  コントローラー内 @ExceptionHandler
      ↓
  ResourceNotFoundException（404）
      ↓
  BusinessException（400）
      ↓
  MethodArgumentNotValidException（400・フィールドエラー付き）
      ↓
  Exception（500・スタックトレースはログのみ）
```

> **セキュリティ原則**: スタックトレースはクライアントに返さない。`log.error("...", ex)` でサーバーサイドに記録するのみ。

---

### 3. `RagChatService.java` — Spring AI RAG パイプライン

**5つの実装パターンを収録**:

| メソッド | パターン | 用途 |
|----------|----------|------|
| `chat()` | 同期チャット + 会話記憶 | 基本的な Q&A |
| `chatStream()` | ストリーミング（`Flux<String>`） | UX向上・長文応答 |
| `chatWithNaiveRag()` | Naive RAG | シンプルな社内文書検索 |
| `chatWithModularRag()` | Modular RAG（クエリ書き換え付き） | 曖昧クエリ対応・高精度 |
| `summarizeToStructured()` | 構造化出力（`record`型） | 型安全な LLM 応答処理 |

**Modular RAG パイプライン**:
```
ユーザー質問
    ↓
RewriteQueryTransformer（LLMでクエリ改善）
    ↓
VectorStoreDocumentRetriever（類似度検索 topK=5, threshold=0.6）
    ↓
ContextualQueryAugmenter（コンテキストをプロンプトへ注入）
    ↓
LLM 応答生成
```

---

### 4. `application.yml` — 外部化設定

| カテゴリ | 設定内容 |
|----------|----------|
| Spring AI | OpenAI / Anthropic プロバイダー切り替え（key の差し替えのみ） |
| VectorStore | PgVector（HNSWインデックス・コサイン類似度） |
| Actuator | Kubernetes プローブ（/health/liveness, /health/readiness） |
| CloudWatch | Micrometer メトリクス送信（batchSize=20 必須） |
| Profiles | dev / prod で設定を自動切り替え |

**プロバイダー切り替え例**:
```yaml
# OpenAI から Anthropic へ切り替えるにはここだけ変更
spring.ai.anthropic.api-key: ${ANTHROPIC_API_KEY}
spring.ai.anthropic.chat.options.model: claude-sonnet-4-5
```

---

## セットアップ

### 必要な依存関係（pom.xml 抜粋）

```xml
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.4.0</version>
</parent>

<dependencies>
    <!-- Web API -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <!-- Bean Validation -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-validation</artifactId>
    </dependency>
    <!-- Actuator -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-actuator</artifactId>
    </dependency>
    <!-- Spring AI - OpenAI -->
    <dependency>
        <groupId>org.springframework.ai</groupId>
        <artifactId>spring-ai-openai-spring-boot-starter</artifactId>
    </dependency>
    <!-- Spring AI - PgVector -->
    <dependency>
        <groupId>org.springframework.ai</groupId>
        <artifactId>spring-ai-pgvector-store-spring-boot-starter</artifactId>
    </dependency>
    <!-- Lombok -->
    <dependency>
        <groupId>org.projectlombok</groupId>
        <artifactId>lombok</artifactId>
        <optional>true</optional>
    </dependency>
</dependencies>

<!-- Spring AI BOM（バージョン管理） -->
<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>org.springframework.ai</groupId>
            <artifactId>spring-ai-bom</artifactId>
            <version>1.1.0</version>
            <type>pom</type>
            <scope>import</scope>
        </dependency>
    </dependencies>
</dependencyManagement>
```

### 起動

```bash
# 環境変数を設定して起動
export OPENAI_API_KEY=sk-...
export DB_URL=jdbc:postgresql://localhost:5432/mydb

# Maven
./mvnw spring-boot:run -Dspring-boot.run.profiles=dev

# JAR ビルド & 実行
./mvnw clean package
java -jar target/springboot-demo-0.0.1-SNAPSHOT.jar --spring.profiles.active=prod
```

### Docker（ECS Fargate 向け最適化ビルド）

```bash
# Cloud Native Buildpacks（Dockerfile不要）
./mvnw spring-boot:build-image \
  -Dspring-boot.build-image.env.BP_SPRING_AOT_ENABLED=true \
  -Dspring-boot.build-image.env.BP_JVM_AOTCACHE_ENABLED=true

# 起動時間の目安: 通常2.38s → AOT+Cache: 0.58s → Native: 0.12s
```

---

## Spring Boot vs 他フレームワーク 選択指針

| 要件 | 推奨 |
|------|------|
| ML/AI ライブラリと同一プロセス統合（PyTorch等） | **FastAPI**（Python） |
| エンタープライズ・セキュリティ・コンプライアンス重視 | **Spring Boot** |
| Kubernetes・メモリ効率・クラウドネイティブ | **Quarkus** |
| TypeScript・フロントエンドとの型共有 | **NestJS** |
| AI API 呼び出し + エンタープライズ統合（本サンプル） | **Spring Boot + Spring AI** |

> Spring Boot は Java/Kotlin の習得コストがあるが、エンタープライズ向けの成熟したエコシステム・Spring AI による LLM 統合・Spring Security による認証認可が強みです。AIエンジニアとして **Python（FastAPI）をメイン**にしつつ、エンタープライズ案件では Spring Boot も扱えると市場価値が高まります。


---

📝 [Notionで詳細を見る]()
