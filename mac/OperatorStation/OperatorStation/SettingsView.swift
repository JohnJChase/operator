import AppKit
import SwiftUI

struct SettingsView: View {
    @ObservedObject var settings: StationSettings
    @ObservedObject var bridge: BridgeClient

    private var logText: String {
        bridge.logLines.joined(separator: "\n")
    }

    var body: some View {
        Form {
            Section("Pi") {
                TextField("Pi URL", text: $settings.piURL)
                    .textFieldStyle(.roundedBorder)
                SecureField("Desktop token", text: $settings.token)
                    .textFieldStyle(.roundedBorder)
                TextField("Client ID", text: $settings.clientID)
                    .textFieldStyle(.roundedBorder)
                TextField("Display name", text: $settings.displayName)
                    .textFieldStyle(.roundedBorder)
            }
            Section("Connection") {
                LabeledContent("Status", value: bridge.state.label)
                HStack {
                    Button("Connect") {
                        bridge.start(settings: settings)
                    }
                    .disabled(!settings.isConfigured)
                    Button("Disconnect") {
                        bridge.stop()
                    }
                    Button("Ping notify") {
                        Task { await bridge.requestTestNotify(settings: settings) }
                    }
                    .disabled(!bridge.state.isOnline)
                }
            }
            Section {
                TextEditor(text: logBinding)
                    .font(.system(.body, design: .monospaced))
                    .frame(minHeight: 200, maxHeight: 280)
                    .scrollContentBackground(.hidden)
                    .padding(4)
                    .background(Color(nsColor: .textBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(Color(nsColor: .separatorColor))
                    )
            } header: {
                HStack {
                    Text("Log")
                    Spacer()
                    Button("Copy") {
                        copyLog()
                    }
                    .disabled(bridge.logLines.isEmpty)
                    .help("Copy the full log to the clipboard")
                }
            } footer: {
                Text("Select any range and ⌘C, or use Copy for the whole log.")
                    .font(.caption)
            }
        }
        .padding()
        .frame(width: 560, height: 520)
    }

    /// Read-only binding so TextEditor stays selectable but edits don't stick.
    private var logBinding: Binding<String> {
        Binding(
            get: {
                bridge.logLines.isEmpty ? "No events yet." : logText
            },
            set: { _ in }
        )
    }

    private func copyLog() {
        let text = logText
        guard !text.isEmpty else { return }
        let board = NSPasteboard.general
        board.clearContents()
        board.setString(text, forType: .string)
    }
}
