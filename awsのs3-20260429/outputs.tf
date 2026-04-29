# PoC品質: 本コードは概念実証用スケルトンです。本番利用前に十分なレビューを行ってください。

output "bucket_id" {
  description = "メインバケット名"
  value       = aws_s3_bucket.main.id
}

output "bucket_arn" {
  description = "メインバケット ARN"
  value       = aws_s3_bucket.main.arn
}

output "bucket_region" {
  description = "メインバケットのリージョン"
  value       = aws_s3_bucket.main.region
}

output "kms_key_arn" {
  description = "SSE-KMS 暗号化キー ARN"
  value       = aws_kms_key.s3.arn
}

output "kms_key_alias" {
  description = "SSE-KMS 暗号化キーエイリアス"
  value       = aws_kms_alias.s3.name
}

output "lambda_presigned_url_function_name" {
  description = "プレサインドURL発行 Lambda 関数名"
  value       = aws_lambda_function.presigned_url.function_name
}

output "lambda_s3_event_processor_function_name" {
  description = "S3イベント処理 Lambda 関数名"
  value       = aws_lambda_function.s3_event_processor.function_name
}

output "api_gateway_invoke_url" {
  description = "プレサインドURL発行 API エンドポイント（POST /upload-url）"
  value       = "${aws_api_gateway_stage.s3_api.invoke_url}/upload-url"
}

output "api_gateway_rest_api_id" {
  description = "API Gateway REST API ID"
  value       = aws_api_gateway_rest_api.s3_api.id
}

output "replica_bucket_id" {
  description = "CRRレプリカバケット名（enable_replication=true の場合）"
  value       = var.enable_replication ? aws_s3_bucket.replica[0].id : null
}

output "replica_bucket_arn" {
  description = "CRRレプリカバケット ARN（enable_replication=true の場合）"
  value       = var.enable_replication ? aws_s3_bucket.replica[0].arn : null
}
