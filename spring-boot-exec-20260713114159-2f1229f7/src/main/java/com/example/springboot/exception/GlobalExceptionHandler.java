// PoC 品質 — 実運用前にセキュリティ・業務ロジックを追加すること

package com.example.springboot.exception;

import lombok.Builder;
import lombok.Data;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.time.Instant;
import java.util.List;
import java.util.stream.Collectors;

/**
 * グローバル例外ハンドリング実装例
 *
 * 設計ポイント:
 *  - @RestControllerAdvice で全コントローラーの例外を一元管理
 *  - 例外型の具体性（ResourceNotFoundException → Exception の順）で優先度制御
 *  - スタックトレースはログに記録するがクライアントには返さない（情報漏洩防止）
 *  - ErrorResponse に timestamp / errorCode を含めて問題追跡を容易にする
 *
 * 例外優先ルール:
 *  コントローラー内 @ExceptionHandler > @RestControllerAdvice（このクラス）
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    // ─── カスタム例外 ──────────────────────────────────────────────────────────

    /**
     * リソース未検出（404）
     * ResourceNotFoundException は下記に定義
     */
    @ExceptionHandler(ResourceNotFoundException.class)
    public ResponseEntity<ErrorResponse> handleNotFound(ResourceNotFoundException ex) {
        log.warn("Resource not found: {}", ex.getMessage());
        return buildResponse(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", ex.getMessage());
    }

    /**
     * ビジネスルール違反（400）
     */
    @ExceptionHandler(BusinessException.class)
    public ResponseEntity<ErrorResponse> handleBusiness(BusinessException ex) {
        log.warn("Business rule violation: {}", ex.getMessage());
        return buildResponse(HttpStatus.BAD_REQUEST, ex.getErrorCode(), ex.getMessage());
    }

    // ─── Spring/Jakarta Validation 例外 ───────────────────────────────────────

    /**
     * @RequestBody バリデーション失敗（400）
     * @Valid 付き @RequestBody が MethodArgumentNotValidException をスローする
     *
     * フィールドエラーを収集し、FieldValidationError のリストとして返却する
     */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ValidationErrorResponse> handleValidation(
            MethodArgumentNotValidException ex) {

        List<ValidationErrorResponse.FieldValidationError> fieldErrors = ex.getBindingResult()
                .getAllErrors()
                .stream()
                .filter(e -> e instanceof FieldError)
                .map(e -> (FieldError) e)
                .map(fe -> ValidationErrorResponse.FieldValidationError.builder()
                        .field(fe.getField())
                        .rejectedValue(fe.getRejectedValue() != null
                                ? fe.getRejectedValue().toString() : "null")
                        .message(fe.getDefaultMessage())
                        .build())
                .collect(Collectors.toList());

        log.debug("Validation failed: {} field error(s)", fieldErrors.size());

        ValidationErrorResponse body = ValidationErrorResponse.builder()
                .timestamp(Instant.now())
                .status(HttpStatus.BAD_REQUEST.value())
                .errorCode("VALIDATION_FAILED")
                .message("入力値に誤りがあります。各フィールドのエラーを確認してください。")
                .fieldErrors(fieldErrors)
                .build();

        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(body);
    }

    // ─── 汎用フォールバック ────────────────────────────────────────────────────

    /**
     * 未捕捉例外（500）
     * スタックトレースはサーバーサイドのみに記録し、クライアントには詳細を返さない
     */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorResponse> handleGeneric(Exception ex) {
        log.error("Unexpected error occurred", ex);
        return buildResponse(
                HttpStatus.INTERNAL_SERVER_ERROR,
                "INTERNAL_ERROR",
                "予期しないエラーが発生しました。しばらくしてから再試行してください。"
        );
    }

    // ─── ヘルパー ──────────────────────────────────────────────────────────────

    private ResponseEntity<ErrorResponse> buildResponse(
            HttpStatus status, String errorCode, String message) {
        ErrorResponse body = ErrorResponse.builder()
                .timestamp(Instant.now())
                .status(status.value())
                .errorCode(errorCode)
                .message(message)
                .build();
        return ResponseEntity.status(status).body(body);
    }

    // ─── レスポンスDTO ─────────────────────────────────────────────────────────

    @Data
    @Builder
    public static class ErrorResponse {
        private Instant timestamp;
        private int status;
        private String errorCode;
        private String message;
    }

    @Data
    @Builder
    public static class ValidationErrorResponse {
        private Instant timestamp;
        private int status;
        private String errorCode;
        private String message;
        private List<FieldValidationError> fieldErrors;

        @Data
        @Builder
        public static class FieldValidationError {
            private String field;
            private String rejectedValue;
            private String message;
        }
    }
}

// ─── カスタム例外クラス群 ─────────────────────────────────────────────────────
// （実際は別ファイルに分けること）

/*
// ResourceNotFoundException.java
package com.example.springboot.exception;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ResponseStatus;

@ResponseStatus(HttpStatus.NOT_FOUND)
public class ResourceNotFoundException extends RuntimeException {
    public ResourceNotFoundException(String message) {
        super(message);
    }
}

// BusinessException.java
package com.example.springboot.exception;

import lombok.Getter;

@Getter
public class BusinessException extends RuntimeException {
    private final String errorCode;

    public BusinessException(String errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
    }

    // 使用例: throw new BusinessException("DUPLICATE_EMAIL", "このメールアドレスは既に使用されています");
}
*/
