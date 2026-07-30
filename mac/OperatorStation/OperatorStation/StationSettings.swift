import Foundation

/// Pi connection settings in UserDefaults.
///
/// The desktop token is the same LAN shared secret as ``OPERATOR_DESKTOP_TOKEN``;
/// Keychain is overkill here and was prompting for the Mac password on every
/// launch (read + rewrite in ``didSet``).
@MainActor
final class StationSettings: ObservableObject {
    private static let defaults = UserDefaults.standard

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
        didSet { Self.defaults.set(token, forKey: "desktopToken") }
    }

    /// When true, register ``open_url`` so digit 7 / Meet can open here.
    /// When false, only ``notify`` — Pi should use ``OPERATOR_MEET_JOIN_TARGET=auto``
    /// to fall back to the handset SIP path.
    @Published var receiveMeetings: Bool {
        didSet { Self.defaults.set(receiveMeetings, forKey: "receiveMeetings") }
    }

    var capabilities: [String] {
        receiveMeetings ? ["open_url", "notify"] : ["notify"]
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
        clientID = Self.defaults.string(forKey: "clientID") ?? "john-macbook"
        displayName = Self.defaults.string(forKey: "displayName") ?? "\(host) Mac"
        token = Self.defaults.string(forKey: "desktopToken") ?? ""
        if Self.defaults.object(forKey: "receiveMeetings") == nil {
            receiveMeetings = true
        } else {
            receiveMeetings = Self.defaults.bool(forKey: "receiveMeetings")
        }
    }
}
