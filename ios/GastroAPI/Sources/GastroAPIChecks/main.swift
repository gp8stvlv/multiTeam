// Foundation-only regression checks for CLT environments without XCTest.
import Foundation
import GastroAPI
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

struct CheckFailure: Error, CustomStringConvertible { let description: String }
func require(_ condition: @autoclosure () -> Bool, _ label: String) throws {
    guard condition() else { throw CheckFailure(description: label) }
}
func bodyData(_ request: URLRequest) throws -> Data {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { throw CheckFailure(description: "Missing request body") }
    stream.open(); defer { stream.close() }
    var data = Data(), buffer = [UInt8](repeating: 0, count: 4096)
    while true {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count == 0 { return data }
        guard count > 0 else { throw stream.streamError ?? CheckFailure(description: "Request stream error") }
        data.append(contentsOf: buffer.prefix(count))
    }
}

final class CheckProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, [String: String], Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw CheckFailure(description: "No handler") }
            let (status, headers, data) = try handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

func makeClient() throws -> APIClient {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [CheckProtocol.self]
    return try APIClient(baseURL: "https://example.test/api/v1", token: "test-token", session: URLSession(configuration: config))
}

let totalsJSON = """
{"calories_kcal":null,"protein_g":"12.75","fat_g":2.25,"carbs_g":null,
 "known_subtotals":{"calories_kcal":"120.5","protein_g":"12.75","fat_g":"2.25","carbs_g":0},
 "complete_fields":["protein_g","fat_g"],"sources":["unknown","estimated"]}
"""

@main struct Checks {
    static func main() async {
        do {
            var passed = 0
            for invalid in ["file:///tmp/key", "https://user:pass@example.test/api/v1", "https://example.test/api/v1?token=secret", "not a url"] {
                do { _ = try APIClient(baseURL: invalid); throw CheckFailure(description: "Accepted invalid baseURL") }
                catch APIClientError.invalidURL { }
            }
            passed += 1
            let totals = try APIClient.decoder().decode(NutritionTotals.self, from: Data(totalsJSON.utf8))
            try require(totals.values.caloriesKcal == nil && totals.values.carbsG == nil, "Unknown totals replaced with zero")
            try require(totals.values.proteinG == Decimal(string: "12.75"), "Decimal string decoding")
            try require(totals.knownSubtotals.caloriesKcal == Decimal(string: "120.5"), "Known subtotal decoding")
            passed += 1
            try require(DisplayValues.minorUnits("900") == 90000, "Money scale")
            try require(DisplayValues.minorUnits("750,50") == 75050, "Comma budget")
            try require(DisplayValues.minorUnits("0.01") == 1, "Minor fraction")
            try require(DisplayValues.minorUnits("1.005") == nil, "Rounded invalid budget")
            try require(DisplayValues.minorUnits("-1") == nil, "Negative budget")
            try require(DisplayValues.minorUnits("900abc") == nil && DisplayValues.decimalInput("NaN") == nil, "Partially parsed invalid decimal")
            passed += 1
            let patch = ProfilePatch(timezone: "Europe/Moscow", favoriteCuisines: [], dislikedIngredients: [],
                                     spicePreference: "any", allergies: [.init(code: "milk")], dailyCaloriesTarget: nil,
                                     defaultBudgetMinor: nil, proactiveEnabled: false)
            let object = try JSONSerialization.jsonObject(with: APIClient.encoder().encode(patch)) as! [String: Any]
            try require(object["daily_calories_target"] is NSNull && object["default_budget_minor"] is NSNull, "Profile clear targets")
            try require(object["user_id"] == nil, "Client supplies user identity")
            passed += 1
            CheckProtocol.handler = { request in
                try require(request.url?.path == "/api/v1/auth/login", "Auth route")
                try require(request.value(forHTTPHeaderField: "Authorization") == nil, "Prior JWT sent to auth")
                return (200, [:], Data("{\"access_token\":\"new-token\",\"token_type\":\"bearer\",\"expires_in\":3600,\"user\":{\"id\":\"u\",\"email\":\"a@b.test\",\"role\":\"user\"}}".utf8))
            }
            let client = try makeClient()
            let auth = try await client.authenticate(email: "a@b.test", password: "test-password", register: false)
            try require(auth.accessToken == "new-token", "Auth response")
            passed += 1
            CheckProtocol.handler = { request in
                try require(request.value(forHTTPHeaderField: "Authorization") == "Bearer test-token", "JWT not passed")
                try require(request.value(forHTTPHeaderField: "Idempotency-Key") == "stable-key", "Missing diary idempotency key")
                let object = try JSONSerialization.jsonObject(with: bodyData(request)) as! [String: Any]
                try require((object["portions"] as? NSNumber)?.decimalValue == Decimal(string: "0.5"), "Fraction lost")
                try require(object["kind"] as? String == "menu_item" && object["user_id"] == nil, "Diary contract identity")
                return (201, [:], Data("""
                {"id":"d","consumed_at":"2026-10-11T12:00:00Z","portions":"0.5","snapshot":{"name":"Демо","menu_item_id":"m",
                  "nutrition_per_portion":{"calories_kcal":null,"protein_g":null,"fat_g":null,"carbs_g":null,"source":"unknown","evidence_ids":[]}},
                 "consumed_nutrition":\(totalsJSON)}
                """.utf8))
            }
            let entry = try await client.addDiary(.init(menuItemId: "m", portions: Decimal(string: "0.5")!), idempotencyKey: "stable-key")
            try require(entry.portions == Decimal(string: "0.5"), "Diary decoding")
            passed += 1
            CheckProtocol.handler = { request in
                let object = try JSONSerialization.jsonObject(with: bodyData(request)) as! [String: Any]
                try require(object["user_id"] == nil, "Recommendation supplies user identity")
                try require(request.value(forHTTPHeaderField: "Idempotency-Key") == "run-key", "Missing recommendation key")
                return (202, ["Retry-After": "4"], Data("{\"id\":\"r\",\"status\":\"queued\",\"trigger\":\"manual\"}".utf8))
            }
            let submitted = try await client.createRecommendation(.init(query: "хинкали", constraints: .init(budgetMinor: 90000)), idempotencyKey: "run-key")
            try require(submitted.retryAfter == 4 && !submitted.run.isTerminal, "202 retry-after")
            passed += 1
            var polls = 0
            CheckProtocol.handler = { _ in
                polls += 1
                return (200, [:], Data("{\"id\":\"r\",\"status\":\"failed\",\"trigger\":\"manual\",\"error\":{\"code\":\"llm_unavailable\",\"message\":\"Unavailable\"}}".utf8))
            }
            let failed = try await client.waitForRecommendation(id: "r")
            try require(polls == 1 && failed.error?.code == "llm_unavailable", "Failed run polling")
            passed += 1
            CheckProtocol.handler = { _ in (401, [:], Data("{\"code\":\"unauthorized\",\"message\":\"expired\"}".utf8)) }
            do { _ = try await client.profile(); throw CheckFailure(description: "401 accepted") }
            catch let error as APIClientError { try require(error.isUnauthorized, "401 identification") }
            passed += 1
            CheckProtocol.handler = { request in
                try require(request.httpMethod == "DELETE", "DELETE method")
                return (204, [:], Data())
            }
            try await client.deleteDiary(id: "d")
            passed += 1
            print("PASS: \(passed) Foundation API regression groups")
        } catch {
            fputs("FAIL: \(error)\n", stderr)
            exit(1)
        }
    }
}
