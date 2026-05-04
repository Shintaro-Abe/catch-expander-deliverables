# =============================================================================
# PoC品質 (Proof of Concept)
# 本番環境での利用前に、セキュリティレビュー・詳細設定・業務ロジックの実装が必要です
# =============================================================================

# =============================================================================
# Amazon Bedrock Agent: AIエージェントのコアコンポーネント
# -----------------------------------------------------------------------------
# Bedrock Agentは「AIエージェント = モデル + ハーネス」における推論エンジン
#
# 処理フロー (エージェントループ):
#   1. ユーザー入力受信
#   2. LLMによる推論 (「次に何をすべきか」を決定)
#   3. Action Group (Lambda) を呼び出してツールを実行
#   4. ツール実行結果をLLMにフィードバック
#   5. タスク完了と判断するまで 2〜4 を反復 (ReActパターン)
#   6. 最終レスポンスを返却
#
# foundation_model: 推論に使用するLLM
# agent_resource_role_arn: BedrockがLambda等を呼び出すためのIAMロール
# instruction: システムプロンプト (モデルの役割・制約・行動指針を定義)
# idle_session_ttl_in_seconds: 非アクティブなセッションを自動クリーンアップ
# =============================================================================
resource "aws_bedrockagent_agent" "harness_agent" {
  agent_name              = "${var.project_name}-agent"
  agent_resource_role_arn = aws_iam_role.bedrock_agent_execution_role.arn
  foundation_model        = var.foundation_model_id
  instruction             = var.agent_instruction

  idle_session_ttl_in_seconds = var.session_ttl_seconds

  description = "AIエージェントハーネスのコアエージェント (PoC)"
}

# =============================================================================
# Bedrock Agent エイリアス
# -----------------------------------------------------------------------------
# エイリアス: エージェントの特定バージョン/ドラフトへの安定した参照ポイント
# 本番デプロイ後にバージョンを切り替えても、API呼び出し側の設定変更が不要になる
# (ブルー/グリーンデプロイやロールバックが容易になる)
# =============================================================================
resource "aws_bedrockagent_agent_alias" "harness_agent_alias" {
  agent_alias_name = "live"
  agent_id         = aws_bedrockagent_agent.harness_agent.agent_id

  description = "本番向けエイリアス (DRAFTバージョンを参照)"
}

# =============================================================================
# Bedrock Agent Action Group: ツール管理 (Tool Management)
# -----------------------------------------------------------------------------
# Action Group = エージェントが使用できる「ツールのセット」
# エージェントはFunction Schemaを見て「どのツールがあるか」「何を渡すべきか」を判断
#
# ツール設計の原則:
#   - ツールは少ないほど良い (モデルの混乱を防ぐ)
#   - 名前と説明を明確にする (モデルへの自然言語インターフェース)
#   - 各ツールは単一の責務を持つ
#
# function_schema: ツール関数の定義 (名前・説明・パラメータ)
# action_group_executor: ツールを実際に実行するLambda関数のARN
# =============================================================================
resource "aws_bedrockagent_agent_action_group" "tool_action_group" {
  agent_id          = aws_bedrockagent_agent.harness_agent.agent_id
  agent_version     = "DRAFT"
  action_group_name = "${var.project_name}-tools"
  description       = "エージェントが使用可能なツール群 (検索・状態管理)"

  # action_group_executor: ツール呼び出しを受け取るLambdaのARN
  action_group_executor {
    lambda = aws_lambda_function.tool_executor.arn
  }

  # function_schema: エージェント（LLM）がツールを認識するためのスキーマ定義
  # 名前と説明は自然言語で記述 → LLMがどのツールをいつ使うか判断する手がかり
  function_schema {
    member_functions {

      # ツール1: 知識ベース検索
      # クエリを受け取り、社内ドキュメント/外部APIから関連情報を検索して返す
      functions {
        name        = "search_knowledge"
        description = "知識ベースや外部ソースから指定クエリに関連する情報を検索して返す"

        parameters {
          map_block_key = "query"
          type          = "string"
          description   = "検索したいキーワードまたは質問文"
          required      = true
        }

        parameters {
          map_block_key = "max_results"
          type          = "integer"
          description   = "返却する最大結果件数 (デフォルト: 5)"
          required      = false
        }
      }

      # ツール2: セッション状態の保存
      # エージェントループの途中結果・中間成果物をDynamoDBに永続化
      # (コンテキストウィンドウの節約 + 障害時のチェックポイント復旧に使用)
      functions {
        name        = "save_session_state"
        description = "現在のタスク進捗・中間成果物をセッションストレージに保存する。長時間タスクの途中経過や重要な計算結果を保持するために使用する"

        parameters {
          map_block_key = "session_id"
          type          = "string"
          description   = "保存先のセッションID"
          required      = true
        }

        parameters {
          map_block_key = "state_data"
          type          = "string"
          description   = "保存するデータ (JSON文字列形式)"
          required      = true
        }

        parameters {
          map_block_key = "step_name"
          type          = "string"
          description   = "現在実行中のステップ名 (タスク追跡用)"
          required      = false
        }
      }

      # ツール3: セッション状態の読み取り
      # 以前のステップで保存した状態を取得し、タスクを継続
      functions {
        name        = "load_session_state"
        description = "以前保存したセッション状態を読み込む。タスクの継続や前のステップの結果を参照する際に使用する"

        parameters {
          map_block_key = "session_id"
          type          = "string"
          description   = "読み込むセッションID"
          required      = true
        }

        parameters {
          map_block_key = "step_name"
          type          = "string"
          description   = "読み込む特定ステップの名前 (省略時は最新の状態を返す)"
          required      = false
        }
      }
    }
  }

  # Action Groupを更新する際は、エージェントを再準備 (PREPARE) する必要がある
  # lifecycle.create_before_destroy: ゼロダウンタイムの更新をサポート
  lifecycle {
    create_before_destroy = true
  }

  depends_on = [aws_lambda_permission.bedrock_invoke_tool]
}

# =============================================================================
# Bedrock Agent 準備 (Prepare)
# -----------------------------------------------------------------------------
# Action Groupやエージェント設定を変更した後、エージェントを「準備完了」状態にする
# null_resource + local-exec でTerraform apply時に自動実行
# (※実際の環境ではCDKのカスタムリソースやCI/CDパイプラインで実装推奨)
# =============================================================================
resource "null_resource" "prepare_agent" {
  triggers = {
    agent_id          = aws_bedrockagent_agent.harness_agent.agent_id
    action_group_hash = aws_bedrockagent_agent_action_group.tool_action_group.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      echo "Preparing Bedrock Agent: ${aws_bedrockagent_agent.harness_agent.agent_id}"
      aws bedrock-agent prepare-agent \
        --agent-id ${aws_bedrockagent_agent.harness_agent.agent_id} \
        --region ${var.aws_region} 2>&1 | tail -1
    EOT
  }

  depends_on = [
    aws_bedrockagent_agent_action_group.tool_action_group,
    aws_bedrockagent_agent_alias.harness_agent_alias,
  ]
}
