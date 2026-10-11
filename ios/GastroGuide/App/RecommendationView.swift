import Foundation
import SwiftUI
import GastroAPI

struct RecommendationView: View {
    @EnvironmentObject private var session: AppState
    @State private var query = "Хочу хинкали, не очень острые"
    @State private var budget = "900"
    @State private var calories = ""
    @State private var mode = "dish_search"
    @State private var run: RecommendationRun?
    @State private var loading = false
    @State private var errorMessage: String?
    @State private var task: Task<Void, Never>?
    @State private var taskGeneration: UUID?
    @State private var consumption: ConsumptionSelection?
    @State private var feed: [FeedItem] = []
    @State private var feedError: String?
    @State private var requestKey = UUID().uuidString
    @State private var pendingRequest: RecommendationCreate?

    var body: some View {
        List {
            Section("Что хочется?") {
                TextField("Например, сытный обед без острого", text: $query, axis: .vertical).lineLimit(2...4)
                Picker("Задача", selection: $mode) {
                    Text("Найти блюдо").tag("dish_search")
                    Text("Собрать приём пищи").tag("meal_plan")
                }
                HStack { Text("Бюджет, ₽"); TextField("Не задан", text: $budget).keyboardType(.decimalPad).multilineTextAlignment(.trailing) }
                HStack { Text("Максимум ккал"); TextField("Не задан", text: $calories).keyboardType(.decimalPad).multilineTextAlignment(.trailing) }
                Text("Предпочтения и аллергии из профиля применяются сервером.").font(.caption).foregroundStyle(.secondary)
                Button { startNewRequest(reuseKey: pendingRequest != nil && run == nil) } label: {
                    HStack { Text("Подобрать"); if loading { Spacer(); ProgressView() } }
                }.disabled(loading || query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }.disabled(loading)
            if loading {
                Section { Text("Агенты ищут блюда и проверяют ограничения…").font(.callout)
                    Button("Прекратить ожидание") { stopWaiting() }
                }
            }
            if let errorMessage {
                Section {
                    ErrorText(message: errorMessage)
                    if run == nil { Button("Повторить запрос") { startNewRequest(reuseKey: true) }.disabled(loading) }
                }
            }
            if let run, !run.isTerminal && !loading {
                Section {
                    Text("Подбор продолжается на сервере.").font(.caption).foregroundStyle(.secondary)
                    Button("Проверить результат") { resume(id: run.id) }
                }
            }
            if let run {
                if run.status == "failed" { Section { ErrorText(message: run.error?.message ?? "Не удалось выполнить подбор.") } }
                if let result = run.result {
                    Section("Результат") {
                        WarningsView(warnings: result.warnings)
                        if result.outcome == "no_matches" { Text("Подходящих вариантов нет. Измените запрос или лимиты.") }
                        ForEach(result.clarifications) { clarification in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(clarification.question).font(.headline)
                                if let values = clarification.suggestedValues { Text(values.joined(separator: " · ")).font(.caption) }
                            }
                        }
                    }
                    ForEach(result.options) { option in
                        Section(option.restaurant.name) {
                            Text(option.restaurant.address).font(.caption).foregroundStyle(.secondary)
                            Text(DisplayValues.money(minor: option.totalPriceMinor, currency: option.currency)).font(.headline)
                            NutritionView(numbers: option.nutrition.values)
                            ForEach(option.items) { item in
                                VStack(alignment: .leading, spacing: 5) {
                                    Text("\(item.name) × \(item.portions)").font(.subheadline)
                                    NutritionSourceView(facts: item.nutritionPerPortion)
                                    Button("Добавить съеденное") {
                                        consumption = .init(menuItemId: item.menuItemId, name: item.name, portions: item.portions)
                                    }.buttonStyle(.bordered)
                                }.padding(.vertical, 4)
                            }
                            ForEach(Array(option.reasons.enumerated()), id: \.offset) { _, reason in Text(reason).font(.caption) }
                            WarningsView(warnings: option.warnings)
                            if !option.evidenceIds.isEmpty { NavigationLink("Источники") { EvidenceView(ids: option.evidenceIds) } }
                        }
                    }
                }
            }
            if !feed.isEmpty {
                Section("Персональная лента") {
                    ForEach(feed) { item in
                        Button(item.title) { resume(id: item.recommendationId) }.disabled(loading)
                    }
                }
            }
            if let feedError { Section("Лента") { Text(feedError).font(.caption).foregroundStyle(.secondary) } }
        }
        .navigationTitle("Подобрать еду")
        .sheet(item: $consumption) { ConsumptionSheet(selection: $0) }
        .task { await loadFeed() }
        .onChange(of: query) { _ in resetPendingRequest() }
        .onChange(of: budget) { _ in resetPendingRequest() }
        .onChange(of: calories) { _ in resetPendingRequest() }
        .onChange(of: mode) { _ in resetPendingRequest() }
        .onDisappear { stopWaiting() }
    }

    private func validatedRequest() throws -> RecommendationCreate {
        var amount: Int?
        if !budget.trimmingCharacters(in: .whitespaces).isEmpty {
            guard let parsed = DisplayValues.minorUnits(budget) else { throw APIClientError.invalidInput("Бюджет: укажите сумму с максимум двумя знаками после запятой.") }
            amount = parsed
        }
        var kcal: Double?
        if !calories.trimmingCharacters(in: .whitespaces).isEmpty {
            guard let parsed = DisplayValues.decimalInput(calories), parsed >= 0 else {
                throw APIClientError.invalidInput("Калории: укажите неотрицательное число.")
            }
            kcal = NSDecimalNumber(decimal: parsed).doubleValue
        }
        return .init(mode: mode, query: query.trimmingCharacters(in: .whitespacesAndNewlines),
                     constraints: .init(budgetMinor: amount, maxCaloriesKcal: kcal),
                     contextRequestId: run?.result?.outcome == "needs_clarification" ? run?.id : nil)
    }
    private func startNewRequest(reuseKey: Bool = false) {
        guard !loading else { return }
        if !reuseKey { resetPendingRequest() }
        let generation = UUID()
        taskGeneration = generation
        loading = true; errorMessage = nil
        task = Task {
            defer { finishWaiting(generation: generation) }
            do {
                guard isCurrentTask(generation) else { return }
                let request = try pendingRequest ?? validatedRequest()
                pendingRequest = request
                let client = try session.client()
                run = nil
                let submitted = try await client.createRecommendation(request, idempotencyKey: requestKey)
                guard isCurrentTask(generation) else { return }
                run = submitted.run
                if !submitted.run.isTerminal {
                    let completed = try await client.waitForRecommendation(id: submitted.run.id, interval: submitted.retryAfter)
                    guard isCurrentTask(generation) else { return }
                    run = completed
                }
            } catch {
                if isCurrentTask(generation), !isCancellation(error) { errorMessage = session.report(error) }
            }
        }
    }
    private func resetPendingRequest() { requestKey = UUID().uuidString; pendingRequest = nil }
    private func resume(id: String) {
        guard !loading else { return }
        let generation = UUID()
        taskGeneration = generation
        loading = true; errorMessage = nil
        task = Task {
            defer { finishWaiting(generation: generation) }
            do {
                guard isCurrentTask(generation) else { return }
                let client = try session.client()
                let current = try await client.recommendation(id: id)
                guard isCurrentTask(generation) else { return }
                run = current
                if !current.isTerminal {
                    let completed = try await client.waitForRecommendation(id: id)
                    guard isCurrentTask(generation) else { return }
                    run = completed
                }
            } catch {
                if isCurrentTask(generation), !isCancellation(error) { errorMessage = session.report(error) }
            }
        }
    }
    private func isCurrentTask(_ generation: UUID) -> Bool {
        taskGeneration == generation && !Task.isCancelled
    }
    private func isCancellation(_ error: Error) -> Bool {
        error is CancellationError || (error as? URLError)?.code == .cancelled
    }
    private func finishWaiting(generation: UUID) {
        guard isCurrentTask(generation) else { return }
        taskGeneration = nil; task = nil; loading = false
    }
    private func stopWaiting() {
        taskGeneration = nil
        task?.cancel(); task = nil; loading = false
    }
    private func loadFeed() async {
        do { feed = try await session.client().feed().items; feedError = nil }
        catch { feedError = session.report(error) }
    }
}
