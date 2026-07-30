import Combine
import SwiftUI
import UserNotifications

@main
struct OperatorStationApp: App {
    @ObservedObject private var model = AppModel.shared
    private let notificationDelegate = NotificationDelegate()

    init() {
        UNUserNotificationCenter.current().delegate = notificationDelegate
    }

    var body: some Scene {
        MenuBarExtra {
            MenuBarContent(model: model)
        } label: {
            Label(
                model.bridge.state.isOnline ? "Operator Connected" : "Operator",
                systemImage: model.bridge.state.isOnline ? "phone.fill" : "phone"
            )
        }
        .menuBarExtraStyle(.menu)

        Settings {
            SettingsView(settings: model.settings, bridge: model.bridge)
        }
    }
}

/// Single shared model so MenuBarExtra and Settings cannot diverge.
@MainActor
final class AppModel: ObservableObject {
    static let shared = AppModel()

    let settings: StationSettings
    let bridge: BridgeClient
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
        Task {
            await DesktopCommands.requestNotificationPermission()
            if settings.isConfigured {
                bridge.start(settings: settings)
            }
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
}

private struct MenuBarContent: View {
    @ObservedObject var model: AppModel

    var body: some View {
        SettingsLink {
            Text("Settings…")
        }
        .keyboardShortcut(",", modifiers: .command)

        Divider()

        Button(model.bridge.state.isOnline || model.bridge.state == .connecting ? "Disconnect" : "Connect") {
            if model.bridge.state.isOnline || model.bridge.state == .connecting {
                model.bridge.stop()
            } else {
                model.bridge.start(settings: model.settings)
            }
        }

        Text(model.bridge.state.label)

        if !model.bridge.lastEvent.isEmpty {
            Text(model.bridge.lastEvent)
                .lineLimit(2)
        }

        Divider()

        Button("Quit Operator Station") {
            model.bridge.stop()
            NSApplication.shared.terminate(nil)
        }
        .keyboardShortcut("q", modifiers: .command)
    }
}
