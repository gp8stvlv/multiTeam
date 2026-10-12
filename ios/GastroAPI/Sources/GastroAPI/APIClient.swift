import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum APIClientError: Error, LocalizedError, Sendable {
    case invalidURL
    case invalidResponse
    case http(status: Int, error: ServerError?)
    case timeout(runId: String)
    case invalidInput(String)

    public var errorDescription: String? {
        switch self {
        case .invalidURL: return "Укажите HTTP(S) адрес API, например http://127.0.0.1:8080/api/v1."
        case .invalidResponse: return "Сервер вернул неожиданный ответ."
        case .http(let status, let error):
            if status == 403, ["invalid_api_key", "api_key_required"].contains(error?.code ?? "") {
                return "Проверьте API-токен в настройках подключения"
            }
            if status == 401 { return "Сессия истекла. Войдите заново." }
            return error.map { "\($0.message)" } ?? "Ошибка сервера (\(status))."
        case .timeout: return "Подбор ещё выполняется. Проверьте результат повторно."
        case .invalidInput(let message): return message
        }
    }
    public var isUnauthorized: Bool {
        if case .http(let status, _) = self { return status == 401 }
        return false
    }
    public var isAPIKeyRejected: Bool {
        if case .http(let status, let error) = self {
            return status == 403 && ["invalid_api_key", "api_key_required"].contains(error?.code ?? "")
        }
        return false
    }
}

public struct RecommendationSubmission: Sendable {
    public let run: RecommendationRun
    public let retryAfter: TimeInterval
}

/// Immutable client. Construct a new instance when the URL, JWT or API key changes.
public final class APIClient: @unchecked Sendable {
    public let baseURL: URL
    private let token: String?
    private let apiKey: String?
    private let session: URLSession

    public init(baseURL: String, token: String? = nil, apiKey: String? = nil,
                session: URLSession = URLSession(configuration: .ephemeral)) throws {
        guard let components = URLComponents(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(components.scheme?.lowercased() ?? ""),
              components.host != nil, components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              let url = components.url else { throw APIClientError.invalidURL }
        let normalizedKey = apiKey?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let normalizedKey, normalizedKey.rangeOfCharacter(from: .controlCharacters) != nil {
            throw APIClientError.invalidInput("API-токен содержит недопустимые символы.")
        }
        self.baseURL = url; self.token = token
        self.apiKey = normalizedKey?.isEmpty == false ? normalizedKey : nil
        self.session = session
    }

    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }
    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }

    private func send<Response: Decodable>(path: String, method: String = "GET", body: Data? = nil,
                                          query: [URLQueryItem] = [], idempotencyKey: String? = nil,
                                          authenticated: Bool = true) async throws -> (Response, HTTPURLResponse) {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)
        if !query.isEmpty { components?.queryItems = query }
        guard let url = components?.url else { throw APIClientError.invalidURL }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 35
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let apiKey { request.setValue(apiKey, forHTTPHeaderField: "X-API-Key") }
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if authenticated, let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let idempotencyKey { request.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key") }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw APIClientError.invalidResponse }
        guard (200..<300).contains(response.statusCode) else {
            throw APIClientError.http(status: response.statusCode, error: try? Self.decoder().decode(ServerError.self, from: data))
        }
        return (try Self.decoder().decode(Response.self, from: data), response)
    }

    private func get<Response: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> Response {
        try await send(path: path, query: query).0
    }

    public func authenticate(email: String, password: String, register: Bool) async throws -> AuthResponse {
        try await send(path: register ? "auth/register" : "auth/login", method: "POST",
                       body: Self.encoder().encode(AuthRequest(email: email, password: password)), authenticated: false).0
    }
    public func profile() async throws -> Profile { try await get("me/profile") }
    public func updateProfile(_ patch: ProfilePatch) async throws -> Profile {
        try await send(path: "me/profile", method: "PATCH", body: Self.encoder().encode(patch)).0
    }
    public func restaurants(limit: Int = 50, offset: Int = 0) async throws -> Page<Restaurant> {
        try await get("restaurants", query: [.init(name: "limit", value: String(limit)), .init(name: "offset", value: String(offset))])
    }
    public func menu(restaurantId: String) async throws -> MenuResponse {
        try await get("restaurants/\(restaurantId)/menu")
    }
    public func menuItem(id: String) async throws -> MenuItem { try await get("menu-items/\(id)") }
    public func createRecommendation(_ request: RecommendationCreate, idempotencyKey: String) async throws -> RecommendationSubmission {
        let (run, response): (RecommendationRun, HTTPURLResponse) = try await send(
            path: "recommendations", method: "POST", body: Self.encoder().encode(request), idempotencyKey: idempotencyKey)
        let retryAfter = max(1, min(60, Double(response.value(forHTTPHeaderField: "Retry-After") ?? "2") ?? 2))
        return .init(run: run, retryAfter: retryAfter)
    }
    public func recommendation(id: String) async throws -> RecommendationRun {
        try await get("recommendations/\(id)")
    }
    /// Cancellation interrupts only polling. The server's job can be resumed using its ID.
    public func waitForRecommendation(id: String, interval: TimeInterval = 2,
                                      timeout: TimeInterval = 180) async throws -> RecommendationRun {
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            try Task.checkCancellation()
            let run = try await recommendation(id: id)
            if run.isTerminal { return run }
            guard Date() < deadline else { throw APIClientError.timeout(runId: id) }
            try await Task.sleep(nanoseconds: UInt64(max(1, interval) * 1_000_000_000))
        }
    }
    public func diary(date: String) async throws -> Page<DiaryEntry> {
        try await get("me/diary", query: [.init(name: "date", value: date), .init(name: "limit", value: "100")])
    }
    public func dailySummary(date: String) async throws -> DailySummary {
        try await get("me/daily-summary", query: [.init(name: "date", value: date)])
    }
    public func addDiary(_ entry: DiaryCreate, idempotencyKey: String) async throws -> DiaryEntry {
        try await send(path: "me/diary", method: "POST", body: Self.encoder().encode(entry), idempotencyKey: idempotencyKey).0
    }
    public func deleteDiary(id: String) async throws {
        // DELETE commonly returns 204; it has no response body to decode.
        let url = baseURL.appendingPathComponent("me/diary/\(id)")
        var request = URLRequest(url: url); request.httpMethod = "DELETE"; request.timeoutInterval = 35
        if let apiKey { request.setValue(apiKey, forHTTPHeaderField: "X-API-Key") }
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw APIClientError.invalidResponse }
        guard (200..<300).contains(response.statusCode) else {
            throw APIClientError.http(status: response.statusCode, error: try? Self.decoder().decode(ServerError.self, from: data))
        }
    }
    public func feed() async throws -> Page<FeedItem> { try await get("me/feed") }
    public func evidence(id: String) async throws -> Evidence { try await get("evidence/\(id)") }
}
