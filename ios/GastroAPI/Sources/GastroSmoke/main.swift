import Foundation
import GastroAPI

@main struct Smoke {
    static func main() async {
        guard let baseURL = ProcessInfo.processInfo.environment["GASTRO_API_URL"] else {
            print("Set GASTRO_API_URL to a running gateway, including /api/v1. This creates a temporary test account and diary record.")
            exit(2)
        }
        do {
            let unauthenticated = try APIClient(baseURL: baseURL)
            let unique = UUID().uuidString.lowercased()
            let auth = try await unauthenticated.authenticate(email: "swift-smoke-\(unique)@example.test",
                                                             password: UUID().uuidString + "-a9", register: true)
            let client = try APIClient(baseURL: baseURL, token: auth.accessToken)
            let profile = try await client.profile()
            let restaurants = try await client.restaurants()
            guard let restaurant = restaurants.items.first else { throw APIClientError.invalidInput("No seeded restaurants") }
            let menu = try await client.menu(restaurantId: restaurant.id)
            guard let item = menu.items.first else { throw APIClientError.invalidInput("No menu items") }
            let key = UUID().uuidString
            let input = DiaryCreate(menuItemId: item.id, portions: Decimal(string: "0.5")!)
            let entry = try await client.addDiary(input, idempotencyKey: key)
            let retry = try await client.addDiary(input, idempotencyKey: key)
            guard entry.id == retry.id else { throw APIClientError.invalidInput("Diary idempotency failed") }
            let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd"
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(identifier: profile.timezone)
            let date = formatter.string(from: Date())
            let diary = try await client.diary(date: date)
            let summary = try await client.dailySummary(date: date)
            guard diary.items.contains(where: { $0.id == entry.id }), summary.entryCount == 1 else {
                throw APIClientError.invalidInput("Diary summary mismatch")
            }
            if let calories = item.nutrition.values.caloriesKcal {
                guard entry.consumedNutrition.values.caloriesKcal == calories * Decimal(string: "0.5")! else {
                    throw APIClientError.invalidInput("Consumed fraction mismatch")
                }
            }
            _ = try await client.feed()
            if let evidenceId = item.evidenceIds.first { _ = try await client.evidence(id: evidenceId) }
            print("PASS: real signup/profile/catalog/menu/evidence/diary/idempotency/summary/feed, JWT-only identity")
            if ProcessInfo.processInfo.environment["GASTRO_SMOKE_RECOMMENDATIONS"] == "1" {
                let created = try await client.createRecommendation(.init(query: "Хочу хинкали", constraints: .init(budgetMinor: 150000)), idempotencyKey: UUID().uuidString)
                let run = created.run.isTerminal ? created.run : try await client.waitForRecommendation(id: created.run.id, interval: created.retryAfter)
                guard run.status == "completed", run.result != nil else {
                    throw APIClientError.invalidInput(run.error?.message ?? "Recommendation failed")
                }
                print("PASS: real recommendation create/poll/result (\(run.result!.outcome))")
            }
            try await client.deleteDiary(id: entry.id)
        } catch {
            fputs("FAIL: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
}
