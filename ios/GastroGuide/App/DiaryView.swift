import SwiftUI
import GastroAPI

struct DiaryView: View {
    @EnvironmentObject private var session: AppState
    @State private var date = Date()
    @State private var entries: [DiaryEntry] = []
    @State private var summary: DailySummary?
    @State private var loading = false
    @State private var errorMessage: String?
    @State private var deletion: DiaryEntry?
    var body: some View {
        List {
            Section {
                DatePicker("День", selection: $date, displayedComponents: .date)
                    .environment(\.timeZone, TimeZone(identifier: session.timezone) ?? .current)
                Text("Часовой пояс дневника: \(session.timezone)").font(.caption).foregroundStyle(.secondary)
            }
            if loading { ProgressView("Обновляем дневник…") }
            if let errorMessage { ErrorText(message: errorMessage); Button("Повторить") { Task { await load() } } }
            if let summary {
                Section("Итого · \(summary.entryCount) записей") {
                    NutritionView(numbers: summary.nutrition.values)
                    if summary.nutrition.values.caloriesKcal == nil {
                        Text("Известный подсчёт: \(DisplayValues.number(summary.nutrition.knownSubtotals.caloriesKcal, suffix: " ккал")); часть данных отсутствует.")
                            .font(.caption).foregroundStyle(.orange)
                    }
                    Text("Ваш ориентир: \(DisplayValues.number(summary.targets.caloriesKcal, suffix: " ккал"))").font(.caption)
                    Text("Осталось: \(DisplayValues.number(summary.remaining.caloriesKcal, suffix: " ккал"))").font(.caption)
                    WarningsView(warnings: summary.warnings)
                }
            }
            Section("Съедено") {
                if entries.isEmpty && !loading { Text("Записей нет. Добавьте съеденное из меню или рекомендации.").foregroundStyle(.secondary) }
                ForEach(entries) { entry in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(entry.snapshot.name).font(.headline)
                        Text("\(DisplayValues.number(entry.portions)) порции · \(time(entry.consumedAt))").font(.caption)
                        NutritionView(numbers: entry.consumedNutrition.values)
                        NutritionSourceView(facts: entry.snapshot.nutritionPerPortion)
                        if let note = entry.note { Text(note).font(.caption).foregroundStyle(.secondary) }
                    }
                    .swipeActions { Button("Удалить", role: .destructive) { deletion = entry } }
                }
            }
        }
        .navigationTitle("Дневник")
        .refreshable { await load() }
        .task(id: "\(session.diaryDate(date)):\(session.diaryRevision):\(session.timezone)") { await load() }
        .confirmationDialog("Удалить запись из дневника?", isPresented: Binding(get: { deletion != nil }, set: { if !$0 { deletion = nil } })) {
            Button("Удалить", role: .destructive) {
                if let entry = deletion { Task { await delete(entry) } }
                deletion = nil
            }
            Button("Отмена", role: .cancel) { deletion = nil }
        }
    }
    private func time(_ timestamp: String) -> String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = parser.date(from: timestamp) ?? ISO8601DateFormatter().date(from: timestamp)
        guard let date else { return timestamp }
        let formatter = DateFormatter()
        formatter.timeZone = TimeZone(identifier: session.timezone) ?? .current
        formatter.dateStyle = .none; formatter.timeStyle = .short
        return formatter.string(from: date)
    }
    private func load() async {
        loading = true; errorMessage = nil
        defer { loading = false }
        do {
            let client = try session.client()
            let day = session.diaryDate(date)
            async let newEntries = client.diary(date: day)
            async let newSummary = client.dailySummary(date: day)
            let result = try await (newEntries, newSummary)
            try Task.checkCancellation()
            entries = result.0.items; summary = result.1
        } catch is CancellationError { }
        catch { errorMessage = session.report(error) }
    }
    private func delete(_ entry: DiaryEntry) async {
        do { try await session.client().deleteDiary(id: entry.id); await load() }
        catch { errorMessage = session.report(error) }
    }
}
