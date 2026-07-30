import AppKit
import Contacts
import ContactsUI
import SwiftUI

/// Picks a phone number from macOS Contacts (one-shot; no sync).
enum MacContactPicker {
    struct Picked: Equatable {
        var name: String
        var e164: String
        var label: String
    }

    @MainActor
    static func pickPhoneNumber() async -> Picked? {
        let status = CNContactStore.authorizationStatus(for: .contacts)
        if status == .notDetermined {
            let store = CNContactStore()
            let ok = (try? await store.requestAccess(for: .contacts)) ?? false
            guard ok else { return nil }
        } else if status != .authorized {
            return nil
        }

        return await withCheckedContinuation { (continuation: CheckedContinuation<Picked?, Never>) in
            let delegate = PickerDelegate { picked in
                continuation.resume(returning: picked)
            }
            // Retain delegate until picker closes.
            PickerDelegate.current = delegate
            let picker = CNContactPicker()
            picker.delegate = delegate
            picker.displayedKeys = [
                CNContactGivenNameKey,
                CNContactFamilyNameKey,
                CNContactOrganizationNameKey,
                CNContactPhoneNumbersKey,
            ]
            let anchor = NSApp.keyWindow?.contentView
                ?? NSApp.windows.first(where: \.isVisible)?.contentView
            if let anchor {
                picker.showRelative(to: anchor.bounds, of: anchor, preferredEdge: .minY)
            } else {
                // No visible window yet (menu-bar only) — pop near the mouse.
                let fallback = NSView(frame: NSRect(x: 0, y: 0, width: 1, height: 1))
                let mouse = NSEvent.mouseLocation
                let panel = NSPanel(
                    contentRect: NSRect(x: mouse.x, y: mouse.y, width: 1, height: 1),
                    styleMask: .borderless,
                    backing: .buffered,
                    defer: false
                )
                panel.isOpaque = false
                panel.backgroundColor = .clear
                panel.contentView = fallback
                panel.orderFront(nil)
                picker.showRelative(to: fallback.bounds, of: fallback, preferredEdge: .minY)
            }
        }
    }

    private final class PickerDelegate: NSObject, CNContactPickerDelegate {
        static var current: PickerDelegate?
        private let onPick: (Picked?) -> Void
        private var finished = false

        init(onPick: @escaping (Picked?) -> Void) {
            self.onPick = onPick
        }

        func contactPicker(_ picker: CNContactPicker, didSelect contact: CNContact) {
            finish(from: contact, phone: contact.phoneNumbers.first)
        }

        func contactPicker(_ picker: CNContactPicker, didSelect contactProperty: CNContactProperty) {
            let contact = contactProperty.contact
            let phone = contactProperty.value as? CNPhoneNumber
            let labeled = contact.phoneNumbers.first { $0.value.stringValue == phone?.stringValue }
            finish(from: contact, phone: labeled)
        }

        func contactPickerDidClose(_ picker: CNContactPicker) {
            finish(nil)
        }

        private func finish(from contact: CNContact, phone: CNLabeledValue<CNPhoneNumber>?) {
            guard let phone else {
                finish(nil)
                return
            }
            let given = contact.givenName
            let family = contact.familyName
            var name = "\(given) \(family)".trimmingCharacters(in: .whitespaces)
            if name.isEmpty {
                name = contact.organizationName.trimmingCharacters(in: .whitespaces)
            }
            if name.isEmpty { name = "Contact" }
            let raw = phone.value.stringValue
            let digits = raw.filter { $0.isNumber || $0 == "+" }
            let label = CNLabeledValue<NSString>.localizedString(forLabel: phone.label ?? CNLabelPhoneNumberMobile)
            finish(Picked(name: name, e164: digits, label: label))
        }

        private func finish(_ picked: Picked?) {
            guard !finished else { return }
            finished = true
            onPick(picked)
            PickerDelegate.current = nil
        }
    }
}
