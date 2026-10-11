import Foundation
import Security
import SwiftUI
import GastroAPI

private struct SavedSession: Codable {
    let token: String
    let expiresAt: Date
}

enum KeychainSession {
    private static let service = "ru.gastrogid.mvp.session"
    private static func query(account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }
    static func read(account: String) -> Data? {
        var query = query(account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }
    static func save(_ data: Data, account: String) throws {
        let base = query(account: account)
        let attributes: [String: Any] = [kSecValueData as String: data,
                                        kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let updated = SecItemUpdate(base as CFDictionary, attributes as CFDictionary)
        if updated == errSecItemNotFound {
            var item = base
            attributes.forEach { item[$0.key] = $0.value }
            let status = SecItemAdd(item as CFDictionary, nil)
            guard status == errSecSuccess else { throw KeychainFailure(status: status) }
        } else if updated != errSecSuccess { throw KeychainFailure(status: updated) }
    }
    static func delete(account: String) { SecItemDelete(query(account: account) as CFDictionary) }
    struct KeychainFailure: LocalizedError {
        let status: OSStatus
        var errorDescription: String? { "Не удалось сохранить сессию в Keychain (\(status))." }
    }
}

@MainActor final class AppState: ObservableObject {
    @Published private(set) var isAuthenticated = false
    @Published private(set) var baseURL: String
    @Published var sessionMessage: String?
    @Published var diaryRevision = 0
    @Published private(set) var timezone = TimeZone.current.identifier
    private var token: String?

    init() {
        baseURL = UserDefaults.standard.string(forKey: "gastro.api.baseURL") ?? "https://90.156.213.217/api/v1"
        restoreSession()
    }
    func client() throws -> APIClient { try APIClient(baseURL: baseURL, token: token) }
    func save(auth: AuthResponse, serverURL: String) throws {
        guard serverURL == baseURL else { throw APIClientError.invalidInput("Сервер изменился во время входа. Повторите вход.") }
        let saved = SavedSession(token: auth.accessToken, expiresAt: Date().addingTimeInterval(Double(auth.expiresIn)))
        try KeychainSession.save(JSONEncoder().encode(saved), account: baseURL)
        token = saved.token; isAuthenticated = true; sessionMessage = nil
    }
    private func restoreSession() {
        guard let data = KeychainSession.read(account: baseURL),
              let saved = try? JSONDecoder().decode(SavedSession.self, from: data), saved.expiresAt > Date() else {
            KeychainSession.delete(account: baseURL)
            token = nil; isAuthenticated = false
            return
        }
        token = saved.token; isAuthenticated = true
    }
    func logout() {
        KeychainSession.delete(account: baseURL)
        token = nil; isAuthenticated = false
        timezone = TimeZone.current.identifier
    }
    func setBaseURL(_ value: String) throws {
        let validated = try APIClient(baseURL: value)
        let normalized = validated.baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard normalized != baseURL else { return }
        logout()
        baseURL = normalized
        UserDefaults.standard.set(normalized, forKey: "gastro.api.baseURL")
        sessionMessage = "Адрес API изменён. Войдите на выбранном сервере."
    }
    func received(profile: Profile) { timezone = profile.timezone }
    func report(_ error: Error) -> String {
        if let apiError = error as? APIClientError, apiError.isUnauthorized {
            logout(); sessionMessage = apiError.localizedDescription
        }
        if error is DecodingError { return "Ответ API не соответствует контракту. Проверьте версию сервера." }
        return error.localizedDescription
    }
    func diaryDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: timezone) ?? .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }
}
