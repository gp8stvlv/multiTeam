import SwiftUI
import GastroAPI

struct ErrorText: View {
    let message: String
    var body: some View { Label(message, systemImage: "exclamationmark.circle").foregroundStyle(.red).font(.callout) }
}

struct NutritionView: View {
    let numbers: NutritionNumbers
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Калории: \(DisplayValues.number(numbers.caloriesKcal, suffix: " ккал"))").font(.subheadline)
            Text("Б: \(DisplayValues.number(numbers.proteinG)) · Ж: \(DisplayValues.number(numbers.fatG)) · У: \(DisplayValues.number(numbers.carbsG)) г")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct NutritionSourceView: View {
    let facts: NutritionFacts
    private var label: String {
        switch facts.source {
        case "restaurant_declared": return "Данные меню"
        case "estimated": return "Приблизительная оценка"
        case "user_entered": return "Введено пользователем"
        default: return "Пищевая ценность неизвестна"
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            if let method = facts.method { Text(method).font(.caption2).foregroundStyle(.secondary) }
        }
    }
}

struct WarningsView: View {
    let warnings: [String]
    var body: some View {
        ForEach(Array(warnings.enumerated()), id: \.offset) { _, warning in
            Label(warning, systemImage: "info.circle").font(.caption).foregroundStyle(.orange)
        }
    }
}

struct ConsumptionSelection: Identifiable {
    let menuItemId: String
    let name: String
    let portions: Int
    var id: String { menuItemId }
}

struct ConsumptionSheet: View {
    let selection: ConsumptionSelection
    @EnvironmentObject private var session: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var portions = "1"
    @State private var consumedAt = Date()
    @State private var note = ""
    @State private var busy = false
    @State private var errorMessage: String?
    @State private var idempotencyKey = UUID().uuidString
    var body: some View {
        NavigationStack {
            Form {
                Section(selection.name) {
                    TextField("Съедено порций", text: $portions).keyboardType(.decimalPad)
                    Text("Можно указать долю: 0,5 — половина стандартной порции.").font(.caption)
                    DatePicker("Когда", selection: $consumedAt)
                    TextField("Заметка", text: $note)
                }.disabled(busy)
                if let errorMessage { Section { ErrorText(message: errorMessage) } }
                Button { Task { await save() } } label: {
                    HStack { Text("Подтвердить: я это съел"); if busy { Spacer(); ProgressView() } }
                }.disabled(busy)
            }
            .navigationTitle("Добавить в дневник")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Отмена") { dismiss() }.disabled(busy) } }
            .onAppear { portions = String(selection.portions) }
            .onChange(of: portions) { _ in idempotencyKey = UUID().uuidString }
            .onChange(of: consumedAt) { _ in idempotencyKey = UUID().uuidString }
            .onChange(of: note) { _ in idempotencyKey = UUID().uuidString }
        }
    }
    private func save() async {
        guard let amount = DisplayValues.decimalInput(portions), amount > 0, amount <= 20 else {
            errorMessage = "Укажите количество больше 0 и не больше 20 порций."; return
        }
        busy = true; errorMessage = nil
        defer { busy = false }
        do {
            _ = try await session.client().addDiary(.init(menuItemId: selection.menuItemId, portions: amount,
                                                         consumedAt: consumedAt, note: note.isEmpty ? nil : note),
                                                   idempotencyKey: idempotencyKey)
            session.diaryRevision += 1
            dismiss()
        } catch { errorMessage = session.report(error) }
    }
}

struct EvidenceView: View {
    let ids: [String]
    @EnvironmentObject private var session: AppState
    @State private var evidence: [Evidence] = []
    @State private var loading = false
    @State private var errorMessage: String?
    var body: some View {
        List {
            if loading { ProgressView() }
            if let errorMessage { ErrorText(message: errorMessage) }
            ForEach(evidence) { item in
                VStack(alignment: .leading, spacing: 8) {
                    Text(item.sourceReference).font(.headline)
                    if let page = item.pageNumber { Text("Страница \(page)").font(.caption) }
                    Text(item.fragmentText).textSelection(.enabled)
                }
            }
        }
        .navigationTitle("Источники")
        .task {
            loading = true
            defer { loading = false }
            do {
                let client = try session.client()
                for id in ids { evidence.append(try await client.evidence(id: id)) }
            } catch { errorMessage = session.report(error) }
        }
    }
}
