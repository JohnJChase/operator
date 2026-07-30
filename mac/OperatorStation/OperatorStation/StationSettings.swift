import Foundation

/// Pi connection settings. Token lives in Keychain; the rest in UserDefaults.
@MainActor
final class StationSettings: ObservableObject {
    private static let defaults = UserDefaults.standard
    private static let tokenAccount = "operator.desktop.token"

    @Published var piURL: String {
        didSet { Self.defaults.set(piURL, forKey: "piURL") }
    }

    @Published var clientID: String {
        didSet { Self.defaults.set(clientID, forKey: "clientID") }
    }

    @Published var displayName: String {
        didSet { Self.defaults.set(displayName, forKey: "displayName") }
    }

    @Published var token: String {
        didSet { Keychain.set(token, account: Self.tokenAccount) }
    }

    var isConfigured: Bool {
        !normalizedBaseURL.isEmpty && !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var normalizedBaseURL: String {
        piURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    init() {
        let host = Host.current().localizedName ?? "Mac"
        piURL = Self.defaults.string(forKey: "piURL") ?? "http://operator.local:8788"
        clientID = Self.defaults.string(forKey: "clientID") ?? host.lowercased().replacingOccurrences(of: " ", with: "-")
        displayName = Self.defaults.string(forKey: "displayName") ?? "\(host) Mac"
        token = Keychain.get(account: Self.tokenAccount) ?? ""
    }
}

enum Keychain {
    private static let service = "com.northleft.OperatorStation"

    static func set(_ value: String, account: String) {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        guard !value.isEmpty else { return }
        var add = query
        add[kSecValueData as String] = data
        SecItemAdd(add as CFDictionary, nil)
    }

    static func get(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
