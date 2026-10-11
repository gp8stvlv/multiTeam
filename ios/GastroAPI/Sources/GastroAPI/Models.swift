import Foundation

/// Decimal input from either JSON numbers or Python's Decimal string representation.
private extension KeyedDecodingContainer {
    func decimalIfPresent(forKey key: Key) throws -> Decimal? {
        guard contains(key), try !decodeNil(forKey: key) else { return nil }
        if let string = try? decode(String.self, forKey: key),
           let value = Decimal(string: string, locale: Locale(identifier: "en_US_POSIX")) { return value }
        return try decode(Decimal.self, forKey: key)
    }
}

public struct AuthRequest: Encodable, Sendable {
    public var email: String
    public var password: String
    public init(email: String, password: String) { self.email = email; self.password = password }
}

public struct User: Decodable, Sendable {
    public var id: String
    public var email: String
    public var role: String
}

public struct AuthResponse: Decodable, Sendable {
    public var accessToken: String
    public var tokenType: String
    public var expiresIn: Int
    public var user: User
}

public struct Page<Item: Decodable & Sendable>: Decodable, Sendable {
    public var items: [Item]
    public var total: Int
    public var limit: Int
    public var offset: Int
}

public struct Allergy: Codable, Identifiable, Sendable {
    public var code: String
    public var note: String?
    public var id: String { code }
    public init(code: String, note: String? = nil) { self.code = code; self.note = note }
}

public struct Profile: Decodable, Sendable {
    public var userId: String
    public var timezone: String
    public var favoriteCuisines: [String]
    public var dislikedIngredients: [String]
    public var spicePreference: String
    public var allergies: [Allergy]
    public var dailyCaloriesTarget: Int?
    public var defaultBudgetMinor: Int?
    public var currency: String
    public var proactiveEnabled: Bool
}

/// Fields represented here are fully replaced. Explicit null clears optional targets.
public struct ProfilePatch: Encodable, Sendable {
    public var timezone: String
    public var favoriteCuisines: [String]
    public var dislikedIngredients: [String]
    public var spicePreference: String
    public var allergies: [Allergy]
    public var dailyCaloriesTarget: Int?
    public var defaultBudgetMinor: Int?
    public var proactiveEnabled: Bool

    public init(timezone: String, favoriteCuisines: [String], dislikedIngredients: [String], spicePreference: String,
                allergies: [Allergy], dailyCaloriesTarget: Int?, defaultBudgetMinor: Int?, proactiveEnabled: Bool) {
        self.timezone = timezone; self.favoriteCuisines = favoriteCuisines
        self.dislikedIngredients = dislikedIngredients; self.spicePreference = spicePreference
        self.allergies = allergies; self.dailyCaloriesTarget = dailyCaloriesTarget
        self.defaultBudgetMinor = defaultBudgetMinor; self.proactiveEnabled = proactiveEnabled
    }

    private enum CodingKeys: String, CodingKey {
        case timezone, favoriteCuisines, dislikedIngredients, spicePreference, allergies
        case dailyCaloriesTarget, defaultBudgetMinor, proactiveEnabled
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(timezone, forKey: .timezone)
        try c.encode(favoriteCuisines, forKey: .favoriteCuisines)
        try c.encode(dislikedIngredients, forKey: .dislikedIngredients)
        try c.encode(spicePreference, forKey: .spicePreference)
        try c.encode(allergies, forKey: .allergies)
        try c.encode(dailyCaloriesTarget, forKey: .dailyCaloriesTarget)
        try c.encode(defaultBudgetMinor, forKey: .defaultBudgetMinor)
        try c.encode(proactiveEnabled, forKey: .proactiveEnabled)
    }
}

public struct Point: Codable, Sendable {
    public var latitude: Double
    public var longitude: Double
    public init(latitude: Double, longitude: Double) { self.latitude = latitude; self.longitude = longitude }
}

public struct Restaurant: Decodable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var address: String
    public var location: Point
    public var cuisines: [String]
    public var rating: Double?
    public var distanceM: Double?
    public var reviewCount: Int
}

public struct NutritionNumbers: Codable, Sendable {
    public var caloriesKcal: Decimal?
    public var proteinG: Decimal?
    public var fatG: Decimal?
    public var carbsG: Decimal?
    private enum CodingKeys: String, CodingKey { case caloriesKcal, proteinG, fatG, carbsG }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        caloriesKcal = try c.decimalIfPresent(forKey: .caloriesKcal)
        proteinG = try c.decimalIfPresent(forKey: .proteinG)
        fatG = try c.decimalIfPresent(forKey: .fatG)
        carbsG = try c.decimalIfPresent(forKey: .carbsG)
    }
}

public struct NutritionFacts: Decodable, Sendable {
    public var values: NutritionNumbers
    public var source: String
    public var method: String?
    public var evidenceIds: [String]
    private enum CodingKeys: String, CodingKey { case source, method, evidenceIds }
    public init(from decoder: Decoder) throws {
        values = try NutritionNumbers(from: decoder)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decode(String.self, forKey: .source)
        method = try c.decodeIfPresent(String.self, forKey: .method)
        evidenceIds = try c.decode([String].self, forKey: .evidenceIds)
    }
}

public struct NutritionTotals: Decodable, Sendable {
    public var values: NutritionNumbers
    public var knownSubtotals: NutritionNumbers
    public var completeFields: [String]
    public var sources: [String]
    private enum CodingKeys: String, CodingKey { case knownSubtotals, completeFields, sources }
    public init(from decoder: Decoder) throws {
        values = try NutritionNumbers(from: decoder)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        knownSubtotals = try c.decode(NutritionNumbers.self, forKey: .knownSubtotals)
        completeFields = try c.decode([String].self, forKey: .completeFields)
        sources = try c.decode([String].self, forKey: .sources)
    }
}

public struct AllergenDeclaration: Decodable, Sendable {
    public var code: String
    public var status: String
    public var evidenceId: String?
}

public struct MenuItem: Decodable, Identifiable, Sendable {
    public var id: String
    public var dishId: String
    public var restaurantId: String
    public var menuVersionId: String
    public var name: String
    public var description: String?
    public var ingredients: [String]
    public var tags: [String]
    public var priceMinor: Int
    public var currency: String
    public var portionWeightG: Double?
    public var allergenInfoStatus: String
    public var allergens: [AllergenDeclaration]
    public var nutrition: NutritionFacts
    public var evidenceIds: [String]
}

public struct MenuVersion: Decodable, Sendable {
    public var id: String
    public var version: Int
    public var status: String
}

public struct MenuResponse: Decodable, Sendable {
    public var menu: MenuVersion
    public var items: [MenuItem]
}

public struct Constraints: Encodable, Sendable {
    public var budgetMinor: Int?
    public var currency: String
    public var maxCaloriesKcal: Double?
    public init(budgetMinor: Int? = nil, currency: String = "RUB", maxCaloriesKcal: Double? = nil) {
        self.budgetMinor = budgetMinor; self.currency = currency; self.maxCaloriesKcal = maxCaloriesKcal
    }
}

public struct RecommendationCreate: Encodable, Sendable {
    public var mode: String
    public var query: String
    public var constraints: Constraints
    public var maxOptions: Int
    public var contextRequestId: String?
    public init(mode: String = "dish_search", query: String, constraints: Constraints = .init(), maxOptions: Int = 3,
                contextRequestId: String? = nil) {
        self.mode = mode; self.query = query; self.constraints = constraints
        self.maxOptions = maxOptions; self.contextRequestId = contextRequestId
    }
}

public struct RecommendationOptionItem: Decodable, Identifiable, Sendable {
    public var menuItemId: String
    public var dishId: String
    public var name: String
    public var portions: Int
    public var unitPriceMinor: Int
    public var nutritionPerPortion: NutritionFacts
    public var id: String { menuItemId }
}

public struct RecommendationOption: Decodable, Identifiable, Sendable {
    public var id: String
    public var rank: Int
    public var restaurant: Restaurant
    public var items: [RecommendationOptionItem]
    public var totalPriceMinor: Int
    public var currency: String
    public var nutrition: NutritionTotals
    public var reasons: [String]
    public var warnings: [String]
    public var evidenceIds: [String]
}

public struct Clarification: Decodable, Identifiable, Sendable {
    public var field: String
    public var question: String
    public var suggestedValues: [String]?
    public var id: String { field }
}

public struct RecommendationResult: Decodable, Sendable {
    public var id: String
    public var outcome: String
    public var options: [RecommendationOption]
    public var clarifications: [Clarification]
    public var warnings: [String]
}

public struct RecommendationRun: Decodable, Identifiable, Sendable {
    public var id: String
    public var status: String
    public var trigger: String
    public var result: RecommendationResult?
    public var error: ServerError?
    public var isTerminal: Bool { status == "completed" || status == "failed" }
}

public struct ServerError: Decodable, Sendable {
    public var code: String
    public var message: String
    public var requestId: String?
}

public struct DiaryCreate: Encodable, Sendable {
    public let kind = "menu_item"
    public var menuItemId: String
    public var consumedAt: String
    public var portions: Decimal
    public var note: String?
    public init(menuItemId: String, portions: Decimal, consumedAt: Date = Date(), note: String? = nil) {
        self.menuItemId = menuItemId; self.portions = portions; self.note = note
        self.consumedAt = ISO8601DateFormatter().string(from: consumedAt)
    }
}

public struct DiarySnapshot: Decodable, Sendable {
    public var name: String
    public var menuItemId: String?
    public var nutritionPerPortion: NutritionFacts
}

public struct DiaryEntry: Decodable, Identifiable, Sendable {
    public var id: String
    public var consumedAt: String
    public var portions: Decimal
    public var snapshot: DiarySnapshot
    public var consumedNutrition: NutritionTotals
    public var note: String?
    private enum CodingKeys: String, CodingKey { case id, consumedAt, portions, snapshot, consumedNutrition, note }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        consumedAt = try c.decode(String.self, forKey: .consumedAt)
        guard let number = try c.decimalIfPresent(forKey: .portions) else {
            throw DecodingError.valueNotFound(Decimal.self, .init(codingPath: c.codingPath, debugDescription: "Missing portions"))
        }
        portions = number
        snapshot = try c.decode(DiarySnapshot.self, forKey: .snapshot)
        consumedNutrition = try c.decode(NutritionTotals.self, forKey: .consumedNutrition)
        note = try c.decodeIfPresent(String.self, forKey: .note)
    }
}

public struct DailySummary: Decodable, Sendable {
    public var date: String
    public var timezone: String
    public var entryCount: Int
    public var nutrition: NutritionTotals
    public var targets: NutritionNumbers
    public var remaining: NutritionNumbers
    public var warnings: [String]
}

public struct FeedItem: Decodable, Identifiable, Sendable {
    public var id: String
    public var recommendationId: String
    public var title: String
    public var status: String
    public var expiresAt: String
}

public struct Evidence: Decodable, Identifiable, Sendable {
    public var id: String
    public var fragmentText: String
    public var sourceReference: String
    public var pageNumber: Int?
    public var sourceKind: String
}

public enum DisplayValues {
    public static func money(minor: Int, currency: String) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = currency
        formatter.locale = Locale(identifier: "ru_RU")
        return formatter.string(from: NSDecimalNumber(value: minor).dividing(by: 100)) ?? "\(minor) \(currency) (minor)"
    }
    public static func number(_ value: Decimal?, suffix: String = "") -> String {
        guard let value else { return "нет данных" }
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "ru_RU")
        formatter.maximumFractionDigits = 1
        return (formatter.string(from: NSDecimalNumber(decimal: value)) ?? "\(value)") + suffix
    }
    public static func decimalInput(_ input: String) -> Decimal? {
        let normalized = input.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: ",", with: ".")
        guard normalized.range(of: #"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$"#, options: .regularExpression) != nil else { return nil }
        return Decimal(string: normalized, locale: Locale(identifier: "en_US_POSIX"))
    }
    /// Budget input must fit whole minor units; no float rounding.
    public static func minorUnits(_ input: String) -> Int? {
        guard let decimal = decimalInput(input), decimal >= 0 else { return nil }
        var scaled = decimal * 100, rounded = Decimal()
        NSDecimalRound(&rounded, &scaled, 0, .plain)
        guard scaled == rounded, rounded <= Decimal(Int.max) else { return nil }
        return NSDecimalNumber(decimal: rounded).intValue
    }
}
