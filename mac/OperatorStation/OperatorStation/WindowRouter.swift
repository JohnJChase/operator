import AppKit
import Foundation
import SwiftUI

extension Notification.Name {
    static let operatorOpenInbox = Notification.Name("operatorOpenInbox")
}

/// Bridges SwiftUI `openWindow` from views that have the Environment value.
@MainActor
enum WindowRouter {
    static var openInbox: (() -> Void)?

    static func install(openInbox: @escaping () -> Void) {
        self.openInbox = openInbox
    }
}

struct WindowRouterInstaller: ViewModifier {
    @Environment(\.openWindow) private var openWindow

    func body(content: Content) -> some View {
        content
            .onAppear {
                WindowRouter.install {
                    openWindow(id: "inbox")
                    NSApp.activate(ignoringOtherApps: true)
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .operatorOpenInbox)) { _ in
                openWindow(id: "inbox")
                NSApp.activate(ignoringOtherApps: true)
            }
    }
}

extension View {
    func installWindowRouter() -> some View {
        modifier(WindowRouterInstaller())
    }
}
