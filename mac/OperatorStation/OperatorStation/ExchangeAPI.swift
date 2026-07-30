import Foundation

struct ExchangeContact: Identifiable, Hashable, Decodable {
    var id: Int
    var name: String
    var e164: String
    var shortCode: String
    var notes: String

    enum CodingKeys: String, CodingKey {
        case id, name, e164, notes
        case shortCode = "short_code"
    }
}

struct InboxSMS: Identifiable, Hashable, Decodable {
    var id: Int
    var direction: String
    var fromE164: String
    var toE164: String
    var fromName: String?
    var toName: String?
    var body: String
    var createdAt: Double
    var heardAt: Double?
    var status: String

    enum CodingKeys: String, CodingKey {
        case id, direction, body, status
        case fromE164 = "from_e164"
        case toE164 = "to_e164"
        case fromName = "from_name"
        case toName = "to_name"
        case createdAt = "created_at"
        case heardAt = "heard_at"
    }

    var displayPeer: String {
        if direction == "in" {
            return fromName?.nilIfEmpty ?? fromE164
        }
        return toName?.nilIfEmpty ?? toE164
    }
}

struct InboxVoicemail: Identifiable, Hashable, Decodable {
    var id: Int
    var fromE164: String
    var fromName: String?
    var createdAt: Double
    var heardAt: Double?
    var durationS: Double
    var status: String
    var audioURL: String

    enum CodingKeys: String, CodingKey {
        case id, status
        case fromE164 = "from_e164"
        case fromName = "from_name"
        case createdAt = "created_at"
        case heardAt = "heard_at"
        case durationS = "duration_s"
        case audioURL = "audio_url"
    }

    var displayFrom: String { fromName?.nilIfEmpty ?? fromE164 }
}

struct InboxPayload: Decodable {
    var sms: [InboxSMS]
    var voicemails: [InboxVoicemail]
    var waiting: Int
}

private extension String {
    var nilIfEmpty: String? {
        let t = trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }
}

enum ExchangeAPIError: LocalizedError {
    case notConfigured
    case badURL
    case http(Int, String)

    var errorDescription: String? {
        switch self {
        case .notConfigured: return "Set Pi URL and token in Settings"
        case .badURL: return "bad URL"
        case .http(let code, let detail):
            return detail.isEmpty ? "HTTP \(code)" : "HTTP \(code): \(detail)"
        }
    }
}

/// Desktop-token HTTP client for inbox / phonebook / place-call.
struct ExchangeAPI {
    var baseURL: String
    var token: String

    private var session: URLSession { URLSession.shared }

    private var normalizedBase: String {
        baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    @MainActor
    static func from(settings: StationSettings) throws -> ExchangeAPI {
        guard settings.isConfigured else { throw ExchangeAPIError.notConfigured }
        return ExchangeAPI(baseURL: settings.normalizedBaseURL, token: settings.token)
    }

    func fetchInbox() async throws -> InboxPayload {
        try await get("/api/inbox")
    }

    func markSMSHeard(_ id: Int) async throws {
        _ = try await post("/api/inbox/sms/heard", ["id": id])
    }

    func deleteSMS(_ id: Int) async throws {
        _ = try await post("/api/inbox/sms/delete", ["id": id])
    }

    func replySMS(id: Int, text: String, confirm: Bool) async throws {
        _ = try await post("/api/inbox/sms/reply", [
            "id": id,
            "text": text,
            "confirm": confirm,
        ] as [String: Any])
    }

    func markVMHeard(_ id: Int) async throws {
        _ = try await post("/api/inbox/vm/heard", ["id": id])
    }

    func deleteVM(_ id: Int) async throws {
        _ = try await post("/api/inbox/vm/delete", ["id": id])
    }

    func downloadVMAudio(id: Int, audioPath: String) async throws -> URL {
        let path = audioPath.hasPrefix("/") ? audioPath : "/api/inbox/vm/\(id)/audio"
        let data = try await getData(path)
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("operator-vm-\(id).wav")
        try data.write(to: url, options: .atomic)
        return url
    }

    func fetchContacts() async throws -> [ExchangeContact] {
        struct Envelope: Decodable { var contacts: [ExchangeContact] }
        let env: Envelope = try await get("/api/phonebook")
        return env.contacts
    }

    func upsertContact(
        id: Int? = nil,
        name: String,
        e164: String,
        shortCode: String = "",
        notes: String = ""
    ) async throws -> ExchangeContact {
        var body: [String: Any] = [
            "name": name,
            "e164": e164,
            "short_code": shortCode,
            "notes": notes,
        ]
        if let id { body["id"] = id }
        struct Envelope: Decodable { var contact: ExchangeContact }
        let env: Envelope = try await postDecode("/api/phonebook", body)
        return env.contact
    }

    func deleteContact(id: Int) async throws {
        _ = try await post("/api/phonebook/delete", ["id": id])
    }

    func placeCall(e164: String) async throws -> String {
        struct Envelope: Decodable { var e164: String }
        let env: Envelope = try await postDecode("/api/place-call", ["e164": e164])
        return env.e164
    }

    // MARK: - HTTP

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let (data, response) = try await session.data(for: try request(path, method: "GET"))
        try throwIfNeeded(response: response, data: data)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func getData(_ path: String) async throws -> Data {
        let (data, response) = try await session.data(for: try request(path, method: "GET"))
        try throwIfNeeded(response: response, data: data)
        return data
    }

    @discardableResult
    private func post(_ path: String, _ body: [String: Any]) async throws -> [String: Any] {
        let data = try await postRaw(path, body)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    private func postDecode<T: Decodable>(_ path: String, _ body: [String: Any]) async throws -> T {
        let data = try await postRaw(path, body)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func postRaw(_ path: String, _ body: [String: Any]) async throws -> Data {
        var req = try request(path, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await session.data(for: req)
        try throwIfNeeded(response: response, data: data)
        return data
    }

    private func request(_ path: String, method: String) throws -> URLRequest {
        guard let url = URL(string: normalizedBase + path) else { throw ExchangeAPIError.badURL }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(token.trimmingCharacters(in: .whitespacesAndNewlines))", forHTTPHeaderField: "Authorization")
        return req
    }

    private func throwIfNeeded(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200 ..< 300).contains(http.statusCode) else {
            let detail = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let clipped = detail.count > 240 ? String(detail.prefix(240)) : detail
            throw ExchangeAPIError.http(http.statusCode, clipped)
        }
    }
}
