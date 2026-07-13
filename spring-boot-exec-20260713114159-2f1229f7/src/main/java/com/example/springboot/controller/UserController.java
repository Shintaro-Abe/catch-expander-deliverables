// PoC 品質 — 実運用前にセキュリティ・業務ロジックを追加すること

package com.example.springboot.controller;

import com.example.springboot.dto.request.CreateUserRequest;
import com.example.springboot.dto.request.UpdateUserRequest;
import com.example.springboot.dto.response.ApiResponse;
import com.example.springboot.dto.response.UserResponse;
import com.example.springboot.service.UserService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * RESTful API ベストプラクティス実装例
 *
 * 設計ポイント:
 *  - URI はリソース名詞（複数形） + HTTPメソッドでアクションを表現
 *  - ResponseEntity<T> で HTTP ステータスを柔軟に制御
 *  - @Valid でリクエストボディのバリデーションを有効化
 *  - ビジネスロジックは Service 層に委譲（コントローラーは薄く保つ）
 *  - ApiResponse<T> ラッパーでレスポンスの一貫性を確保
 */
@RestController
@RequestMapping("/api/v1/users")
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    /**
     * ユーザー一覧取得
     * GET /api/v1/users → 200 OK
     */
    @GetMapping
    public ResponseEntity<ApiResponse<List<UserResponse>>> getUsers() {
        List<UserResponse> users = userService.findAll();
        return ResponseEntity.ok(ApiResponse.success(users));
    }

    /**
     * ユーザー単件取得
     * GET /api/v1/users/{id} → 200 OK / 404 Not Found
     */
    @GetMapping("/{id}")
    public ResponseEntity<ApiResponse<UserResponse>> getUser(@PathVariable Long id) {
        UserResponse user = userService.findById(id);
        return ResponseEntity.ok(ApiResponse.success(user));
    }

    /**
     * ユーザー作成
     * POST /api/v1/users → 201 Created
     * @Valid により CreateUserRequest のバリデーションアノテーションを有効化
     */
    @PostMapping
    public ResponseEntity<ApiResponse<UserResponse>> createUser(
            @Valid @RequestBody CreateUserRequest request) {
        UserResponse created = userService.create(request);
        return ResponseEntity
                .status(HttpStatus.CREATED)
                .body(ApiResponse.success(created));
    }

    /**
     * ユーザー更新（部分更新）
     * PUT /api/v1/users/{id} → 200 OK / 404 Not Found
     */
    @PutMapping("/{id}")
    public ResponseEntity<ApiResponse<UserResponse>> updateUser(
            @PathVariable Long id,
            @Valid @RequestBody UpdateUserRequest request) {
        UserResponse updated = userService.update(id, request);
        return ResponseEntity.ok(ApiResponse.success(updated));
    }

    /**
     * ユーザー削除
     * DELETE /api/v1/users/{id} → 204 No Content / 404 Not Found
     */
    @DeleteMapping("/{id}")
    public ResponseEntity<Void> deleteUser(@PathVariable Long id) {
        userService.delete(id);
        return ResponseEntity.noContent().build();
    }
}

// ─── 内部クラス群（実際はパッケージを分けることを推奨） ────────────────────

// ---------- DTO: リクエスト ----------
// ファイル: dto/request/CreateUserRequest.java
/*
package com.example.springboot.dto.request;

import jakarta.validation.constraints.*;
import lombok.Data;

@Data
public class CreateUserRequest {

    @NotBlank(message = "名前は必須です")
    @Size(min = 2, max = 50, message = "名前は2〜50文字で入力してください")
    private String name;

    @NotBlank(message = "メールアドレスは必須です")
    @Email(message = "正しいメールアドレス形式で入力してください")
    private String email;

    @Min(value = 0, message = "年齢は0以上を入力してください")
    @Max(value = 150, message = "年齢は150以下を入力してください")
    private int age;
}
*/

// ファイル: dto/request/UpdateUserRequest.java
/*
package com.example.springboot.dto.request;

import jakarta.validation.constraints.*;
import lombok.Data;

@Data
public class UpdateUserRequest {
    @Size(min = 2, max = 50)
    private String name;

    @Min(0) @Max(150)
    private Integer age;
}
*/

// ---------- DTO: レスポンス ----------
// ファイル: dto/response/UserResponse.java
/*
package com.example.springboot.dto.response;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class UserResponse {
    private Long id;
    private String name;
    private String email;
    private int age;
}
*/

// ファイル: dto/response/ApiResponse.java
/*
package com.example.springboot.dto.response;

import lombok.AllArgsConstructor;
import lombok.Data;

@Data
@AllArgsConstructor
public class ApiResponse<T> {
    private boolean success;
    private T data;
    private String message;

    public static <T> ApiResponse<T> success(T data) {
        return new ApiResponse<>(true, data, null);
    }

    public static <T> ApiResponse<T> error(String message) {
        return new ApiResponse<>(false, null, message);
    }
}
*/

// ---------- Service スタブ ----------
// ファイル: service/UserService.java
/*
package com.example.springboot.service;

import com.example.springboot.dto.request.CreateUserRequest;
import com.example.springboot.dto.request.UpdateUserRequest;
import com.example.springboot.dto.response.UserResponse;
import com.example.springboot.exception.ResourceNotFoundException;
import org.springframework.stereotype.Service;

import java.util.*;

@Service
public class UserService {

    // 実際は JPA Repository + DB を使用
    private final Map<Long, UserResponse> store = new HashMap<>();
    private long nextId = 1;

    public List<UserResponse> findAll() {
        return new ArrayList<>(store.values());
    }

    public UserResponse findById(Long id) {
        return Optional.ofNullable(store.get(id))
                .orElseThrow(() -> new ResourceNotFoundException("User not found: " + id));
    }

    public UserResponse create(CreateUserRequest req) {
        UserResponse user = UserResponse.builder()
                .id(nextId++)
                .name(req.getName())
                .email(req.getEmail())
                .age(req.getAge())
                .build();
        store.put(user.getId(), user);
        return user;
    }

    public UserResponse update(Long id, UpdateUserRequest req) {
        UserResponse existing = findById(id);
        if (req.getName() != null) existing.setName(req.getName());
        if (req.getAge() != null) existing.setAge(req.getAge());
        store.put(id, existing);
        return existing;
    }

    public void delete(Long id) {
        findById(id); // 存在チェック（なければ ResourceNotFoundException）
        store.remove(id);
    }
}
*/
