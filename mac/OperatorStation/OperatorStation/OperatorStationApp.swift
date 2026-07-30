import AppKit
import Combine
import SwiftUI
import UserNotifications

@main
struct OperatorStationApp: App {
    @ObservedObject private var model = AppModel.shared
    private let notificationDelegate = NotificationDelegate()

    init() {
        let center = UNUserNotificationCenter.current()
        center.delegate = notificationDelegate
    }

    var body: some Scene {
        MenuBarExtra {
            MenuBarContent(model: model)
                .installWindowRouter()
        } label: {
            Label(model.plant.menuLine, systemImage: model.plant.symbolName)
        }
        .menuBarExtraStyle(.menu)

        Settings {
            SettingsView(settings: model.settings, bridge: model.bridge)
                .installWindowRouter()
        }

        Window("Inbox", id: "inbox") {
            InboxView(settings: model.settings)
                .installWindowRouter()
        }
        .defaultSize(width: 520, height: 560)

        Window("Directory", id: "directory") {
            DirectoryView(settings: model.settings)
        }
        .defaultSize(width: 520, height: 560)

        Window("Place Call", id: "place-call") {
            PlaceCallView(settings: model.settings)
        }
        .defaultSize(width: 440, height: 360)

        Window("Meet Priority", id: "routing") {
            RoutingView(settings: model.settings)
        }
        .defaultSize(width: 520, height: 420)
    }
}

@MainActor
final class AppModel: ObservableObject {
    static let shared = AppModel()

    let settings: StationSettings
    let bridge: BridgeClient
    let plantMonitor = PlantStatusMonitor()
    @Published var focusSMSID: Int?
    @Published private(set) var plant = PlantStatus()

    private var cancellables = Set<AnyCancellable>()

    private init() {
        let settings = StationSettings()
        let bridge = BridgeClient()
        self.settings = settings
        self.bridge = bridge
        settings.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        bridge.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        plantMonitor.objectWillChange
            .sink { [weak self] _ in
                self?.plant = self?.plantMonitor.status ?? PlantStatus()
                self?.objectWillChange.send()
            }
            .store(in: &cancellables)
        Task {
            await DesktopCommands.requestNotificationPermission()
            plantMonitor.start(settings: settings, bridge: bridge)
            if settings.isConfigured {
                bridge.start(settings: settings)
            }
        }
    }

    func openInbox(focusMessageID: Int?) {
        focusSMSID = focusMessageID
        NotificationCenter.default.post(
            name: .operatorOpenInbox,
            object: nil,
            userInfo: focusMessageID.map { ["message_id": $0] }
        )
        if let open = WindowRouter.openInbox {
            open()
        } else {
            NSApp.activate(ignoringOtherApps: true)
        }
    }
}

final class NotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .list]
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let info = response.notification.request.content.userInfo
        let messageID = Self.messageID(from: info)
        let openInbox = info["open"] as? String == "inbox"
            || response.notification.request.content.categoryIdentifier == DesktopCommands.smsCategory
            || response.actionIdentifier == "OPEN_INBOX"
            || response.actionIdentifier == UNNotificationDefaultActionIdentifier
        guard openInbox || messageID != nil else { return }
        await MainActor.run {
            AppModel.shared.openInbox(focusMessageID: messageID)
        }
    }

    private static func messageID(from info: [AnyHashable: Any]) -> Int? {
        if let n = info["message_id"] as? Int { return n }
        if let n = info["message_id"] as? NSNumber { return n.intValue }
        return nil
    }
}

private struct MenuBarContent: View {
    @ObservedObject var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Text(model.plant.menuLine)
            .foregroundStyle(.secondary)

        Divider()

        SettingsLink {
            Text("Settings…")
        }
        .keyboardShortcut(",", modifiers: .command)

        Divider()

        Button("Inbox") { openWindow(id: "inbox") }
        Button("Directory") { openWindow(id: "directory") }
        Button("Place call…") { openWindow(id: "place-call") }
        Button("Meet priority…") { openWindow(id: "routing") }

        Divider()

        Button(model.bridge.state.isOnline || model.bridge.state == .connecting ? "Disconnect" : "Connect") {
            if model.bridge.state.isOnline || model.bridge.state == .connecting {
                model.bridge.stop()
            } else {
                model.bridge.start(settings: model.settings)
            }
        }

        Text(model.bridge.state.label)

        Toggle(
            "Open Meet here",
            isOn: Binding(
                get: { model.settings.receiveMeetings },
                set: { newValue in
                    model.settings.receiveMeetings = newValue
                    model.bridge.applySettings(model.settings)
                }
            )
        )

        if !model.bridge.lastEvent.isEmpty {
            Text(model.bridge.lastEvent)
                .lineLimit(2)
        }

        Divider()

        Button("Quit Operator Station") {
            model.bridge.stop()
            model.plantMonitor.stop()
            NSApplication.shared.terminate(nil)
        }
        .keyboardShortcut("q", modifiers: .command)
    }
}
