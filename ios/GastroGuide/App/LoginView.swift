import SwiftUI
import GastroAPI

struct LoginView: View {
    @EnvironmentObject private var session: AppState
    @State private var email = ""
    @State private var password = ""
    @State private var register = false
    @State private var loading = false
    @State private var errorMessage: String?
    @State private var showSettings = false

    var body: some View {
        Form {
            Section {
                Text("Выбирайте блюда по вкусу, бюджету и питанию.").font(.headline)
                Picker("Аккаунт", selection: $register) {
                    Text("Вход").tag(false)
                    Text("Регистрация").tag(true)
                }.pickerStyle(.segmented)
                TextField("Email", text: $email)
                    .textContentType(.username).keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("Пароль", text: $password)
                    .textContentType(register ? .newPassword : .password)
                if register { Text("Минимум 8 символов.").font(.caption).foregroundStyle(.secondary) }
                Button { Task { await authenticate() } } label: {
                    HStack {
                        Text(register ? "Создать аккаунт" : "Войти")
                        if loading { Spacer(); ProgressView() }
                    }
                }.disabled(loading || email.isEmpty || password.isEmpty)
            }
            if let message = errorMessage ?? session.sessionMessage { Section { ErrorText(message: message) } }
            Section("Сервер") {
                Text(session.baseURL).font(.caption).textSelection(.enabled)
                Button("Изменить адрес API") { showSettings = true }.disabled(loading)
            }
        }
        .navigationTitle("ГастроГид")
        .sheet(isPresented: $showSettings) { NavigationStack { ServerSettingsView() } }
    }
    private func authenticate() async {
        loading = true; errorMessage = nil
        defer { loading = false }
        do {
            let requestedServer = session.baseURL
            let client = try session.client()
            let auth = try await client.authenticate(email: email.trimmingCharacters(in: .whitespacesAndNewlines),
                                                     password: password, register: register)
            try session.save(auth: auth, serverURL: requestedServer)
            password = ""
        } catch { errorMessage = session.report(error) }
    }
}

struct ServerSettingsView: View {
    @EnvironmentObject private var session: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var baseURL = ""
    @State private var errorMessage: String?
    var body: some View {
        Form {
            Section("Базовый адрес API") {
                TextField("http://127.0.0.1:8080/api/v1", text: $baseURL)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                Text("Включите /api/v1. Смена сервера завершает текущую сессию.").font(.caption)
                if baseURL.lowercased().hasPrefix("http:") {
                    Text("Учебный сервер использует HTTP: пароль и запросы передаются без TLS. Для эксплуатации укажите HTTPS.")
                        .font(.caption).foregroundStyle(.orange)
                }
            }
            if let errorMessage { ErrorText(message: errorMessage) }
            Button("Сохранить") {
                do { try session.setBaseURL(baseURL); dismiss() }
                catch { errorMessage = session.report(error) }
            }
        }
        .navigationTitle("Подключение")
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() } } }
        .onAppear { baseURL = session.baseURL }
    }
}
