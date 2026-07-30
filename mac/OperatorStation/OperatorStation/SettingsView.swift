import SwiftUI

struct SettingsView: View {
    @ObservedObject var settings: StationSettings
    @ObservedObject var bridge: BridgeClient

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
            Section("Log") {
                if bridge.logLines.isEmpty {
                    Text("No events yet.")
                        .foregroundStyle(.secondary)
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 4) {
                            ForEach(Array(bridge.logLines.enumerated()), id: \.offset) { _, line in
                                Text(line)
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                    .frame(minHeight: 180, maxHeight: 280)
                }
            }
        }
        .padding()
        .frame(width: 520, height: 460)
    }
}
