import Foundation

struct PlantStatus: Equatable {
    var state: String = "—"
    var offHook: Bool = false
    var ringing: Bool = false
    var bridgeOnline: Bool = false
    var updatedAt: Date?

    var menuLine: String {
        if !bridgeOnline {
            return "WE302: (bridge offline)"
        }
        if ringing {
            return "WE302: ringing"
        }
        if offHook {
            return "WE302: off-hook · \(shortState)"
        }
        return "WE302: \(shortState)"
    }

    var shortState: String {
        switch state {
        case "ON_HOOK_IDLE": return "idle"
        case "OFF_HOOK_IDLE", "DIALING", "COLLECTING": return state.lowercased().replacingOccurrences(of: "_", with: " ")
        case "SMS_ALERTING": return "SMS alert"
        case "INCOMING_RINGING": return "incoming"
        case "OUTGOING_RINGING": return "outgoing ring"
        case "SIP_CALL", "SIP_ACTIVE": return "on call"
        case "HOOK_PENDING": return "hook pending"
        default:
            return state.replacingOccurrences(of: "_", with: " ").lowercased()
        }
    }

    var symbolName: String {
        if ringing { return "bell.fill" }
        if offHook { return "phone.arrow.up.right.fill" }
        return bridgeOnline ? "phone.fill" : "phone"
    }
}

@MainActor
final class PlantStatusMonitor: ObservableObject {
    @Published private(set) var status = PlantStatus()

    private var task: Task<Void, Never>?
    private weak var settings: StationSettings?
    private weak var bridge: BridgeClient?

    func start(settings: StationSettings, bridge: BridgeClient) {
        self.settings = settings
        self.bridge = bridge
        stop()
        task = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    func refresh() async {
        guard let settings, settings.isConfigured else {
            status = PlantStatus(bridgeOnline: bridge?.state.isOnline ?? false)
            return
        }
        let bridgeOnline = bridge?.state.isOnline ?? false
        do {
            let api = try ExchangeAPI.from(settings: settings)
            let raw = try await api.fetchStatus()
            status = PlantStatus(
                state: raw.state,
                offHook: raw.offHook,
                ringing: raw.ringing,
                bridgeOnline: bridgeOnline,
                updatedAt: Date()
            )
        } catch {
            status = PlantStatus(
                state: status.state,
                offHook: status.offHook,
                ringing: status.ringing,
                bridgeOnline: bridgeOnline,
                updatedAt: status.updatedAt
            )
        }
    }
}
