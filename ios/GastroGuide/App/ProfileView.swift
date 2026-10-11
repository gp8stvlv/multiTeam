import SwiftUI
import GastroAPI

struct ProfileView: View {
    @EnvironmentObject private var session: AppState
    @State private var cuisines = ""
    @State private var dislikes = ""
    @State private var allergies = ""
    @State private var originalAllergies: [Allergy] = []
    @State private var spice = "any"
    @State private var timezone = TimeZone.current.identifier
    @State private var calories = ""
    @State private var budget = ""
    @State private var proactive = false
    @State private var loading = false
    @State private var loaded = false
    @State private var saved = false
    @State private var errorMessage: String?
    @State private var showSettings = false
    var body: some View {
        Form {
            if loading { ProgressView() }
            if let errorMessage { Section { ErrorText(message: errorMessage) } }
            if !loaded { Button("Загрузить профиль") { Task { await load() } }.disabled(loading) }
            Section("Предпочтения") {
                TextField("Любимые кухни через запятую", text: $cuisines)
                TextField("Нежелательные ингредиенты", text: $dislikes)
                Picker("Острое", selection: $spice) {
                    Text("Без острого").tag("none")
                    Text("Немного").tag("mild")
                    Text("Любое").tag("any")
                }
                TextField("Коды аллергий через запятую", text: $allergies)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                Text("Например: wheat, milk, peanut, egg, fish, soy. При неполном составе сервер не подтверждает соответствие ограничениям.")
                    .font(.caption).foregroundStyle(.secondary)
            }.disabled(!loaded || loading)
            Section("Ваши ориентиры") {
                TextField("Калории в день (необязательно)", text: $calories).keyboardType(.numberPad)
                TextField("Бюджет на приём пищи, ₽", text: $budget).keyboardType(.decimalPad)
                TextField("Часовой пояс IANA", text: $timezone).textInputAutocapitalization(.never).autocorrectionDisabled()
                Toggle("Персональная лента", isOn: $proactive)
                Text("Пустое поле сбрасывает ориентир. Значения задаёте вы.").font(.caption).foregroundStyle(.secondary)
            }.disabled(!loaded || loading)
            if loaded {
                Button("Сохранить профиль") { Task { await save() } }.disabled(loading)
                if saved { Label("Сохранено", systemImage: "checkmark.circle").foregroundStyle(.green) }
            }
            Section("Подключение") {
                Text(session.baseURL).font(.caption).textSelection(.enabled)
                Button("Адрес API") { showSettings = true }
                Button("Выйти", role: .destructive) { session.logout() }
            }
        }
        .navigationTitle("Профиль")
        .task { await load() }
        .sheet(isPresented: $showSettings) { NavigationStack { ServerSettingsView() } }
    }
    private func list(_ string: String) -> [String] {
        let values = string.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        return values.reduce(into: []) { result, value in if !result.contains(value) { result.append(value) } }
    }
    private func load() async {
        loading = true; errorMessage = nil
        defer { loading = false }
        do {
            let profile = try await session.client().profile()
            session.received(profile: profile)
            cuisines = profile.favoriteCuisines.joined(separator: ", ")
            dislikes = profile.dislikedIngredients.joined(separator: ", ")
            allergies = profile.allergies.map(\.code).joined(separator: ", ")
            originalAllergies = profile.allergies
            spice = profile.spicePreference; timezone = profile.timezone
            calories = profile.dailyCaloriesTarget.map(String.init) ?? ""
            budget = profile.defaultBudgetMinor.map { NSDecimalNumber(value: $0).dividing(by: 100).stringValue } ?? ""
            proactive = profile.proactiveEnabled
            loaded = true
        } catch { errorMessage = session.report(error) }
    }
    private func save() async {
        errorMessage = nil; saved = false
        let kcal: Int?
        if calories.trimmingCharacters(in: .whitespaces).isEmpty { kcal = nil }
        else if let value = Int(calories), value > 0 { kcal = value }
        else { errorMessage = "Калории: укажите положительное целое число или очистите поле."; return }
        let amount: Int?
        if budget.trimmingCharacters(in: .whitespaces).isEmpty { amount = nil }
        else if let value = DisplayValues.minorUnits(budget) { amount = value }
        else { errorMessage = "Бюджет: укажите неотрицательную сумму с максимум двумя знаками после запятой."; return }
        guard TimeZone(identifier: timezone) != nil else { errorMessage = "Неизвестный часовой пояс IANA."; return }
        loading = true
        defer { loading = false }
        do {
            let newAllergies = list(allergies.lowercased()).map { code in
                originalAllergies.first(where: { $0.code == code }) ?? Allergy(code: code)
            }
            let patch = ProfilePatch(timezone: timezone, favoriteCuisines: list(cuisines), dislikedIngredients: list(dislikes),
                                     spicePreference: spice, allergies: newAllergies,
                                     dailyCaloriesTarget: kcal, defaultBudgetMinor: amount, proactiveEnabled: proactive)
            let profile = try await session.client().updateProfile(patch)
            session.received(profile: profile); saved = true
        } catch { errorMessage = session.report(error) }
    }
}
