import AppKit
import Foundation
import Observation
import SwiftUI

enum AppLanguage: String, CaseIterable, Identifiable {
    case simplifiedChinese = "zh-Hans"
    case english = "en"

    var id: String { rawValue }
    var name: String { self == .english ? "English" : "简体中文" }
    var locale: Locale { Locale(identifier: rawValue) }
}

@MainActor @Observable
final class LanguageSettings {
    static var shared = LanguageSettings()
    static let defaultsKey = "uiLanguage"
    private let defaults: UserDefaults
    var language: AppLanguage {
        didSet {
            guard language != oldValue else { return }
            defaults.set(language.rawValue, forKey: Self.defaultsKey)
            NotificationCenter.default.post(name: L10n.didChange, object: self)
        }
    }

    init(defaults: UserDefaults? = nil) {
        let defaults = defaults ?? UserDefaults.standard.string(forKey: "defaultsSuite")
            .flatMap(UserDefaults.init(suiteName:)) ?? .standard
        self.defaults = defaults
        language = defaults.string(forKey: Self.defaultsKey).flatMap(AppLanguage.init(rawValue:))
            ?? .simplifiedChinese
    }
}

/// English source templates are stable keys. Interpolated values never become lookup keys.
enum L10n {
    static let didChange = Notification.Name("DigUpLanguageDidChange")
    static var locale: Locale { LanguageSettings.shared.language.locale }
    static let catalog: [String: String] = {
        // SwiftPM's generated accessor looks beside the executable; packaged apps keep resources in Contents/Resources.
        let packaged = Bundle.main.url(forResource: "DigUp_DigUpApp", withExtension: "bundle")
            .flatMap(Bundle.init(url:))
        let bundle = packaged ?? (Bundle.main.bundleURL.pathExtension == "app" ? .main : .module)
        guard let url = bundle.url(forResource: "zh-Hans", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let values = try? JSONDecoder().decode([String: String].self, from: data) else {
            assertionFailure("Missing DigUp localization resources")
            return [:]
        }
        return values
    }()

    struct Message: ExpressibleByStringLiteral, ExpressibleByStringInterpolation, Sendable {
        let key: String
        let arguments: [String]

        init(stringLiteral value: String) { key = value; arguments = [] }
        init(stringInterpolation value: StringInterpolation) {
            key = value.key
            arguments = value.arguments
        }

        struct StringInterpolation: StringInterpolationProtocol {
            var key = ""
            var arguments: [String] = []
            init(literalCapacity: Int, interpolationCount: Int) {
                key.reserveCapacity(literalCapacity)
                arguments.reserveCapacity(interpolationCount)
            }
            mutating func appendLiteral(_ literal: String) { key += literal }
            mutating func appendInterpolation<T>(_ value: T) {
                key += "{\(arguments.count)}"
                arguments.append(String(describing: value))
            }
        }
    }

    static func tr(_ message: Message) -> String {
        render(message.key, arguments: message.arguments, language: LanguageSettings.shared.language)
    }

    static func render(_ key: String, arguments: [String] = [], language: AppLanguage) -> String {
        let template = language == .english ? key : catalog[key] ?? key
        // Replace tokens in the template, not in inserted values (a filename may itself contain "{1}").
        let expression = try! NSRegularExpression(pattern: #"\{(\d+)\}"#)
        let matches = expression.matches(in: template, range: NSRange(template.startIndex..., in: template))
        var result = template
        for match in matches.reversed() {
            guard let range = Range(match.range, in: result),
                  let numberRange = Range(match.range(at: 1), in: template),
                  let index = Int(template[numberRange]), arguments.indices.contains(index) else { continue }
            result.replaceSubrange(range, with: arguments[index])
        }
        return result
    }

    /// Legacy app-owned status/error text is kept in English at rest, so it can refresh in either language.
    /// Do not use this for user documents, queries, excerpts, paths, or filenames.
    static func resolve(_ text: String) -> String {
        guard LanguageSettings.shared.language != .english else { return text }
        if let translation = catalog[text] { return translation }
        for key in catalog.keys.sorted() where key.contains("{0}") {
            let token = try! NSRegularExpression(pattern: #"\{\d+\}"#)
            let ranges = token.matches(in: key, range: NSRange(key.startIndex..., in: key))
            var pattern = ""
            var start = key.startIndex
            for match in ranges {
                guard let range = Range(match.range, in: key) else { continue }
                pattern += NSRegularExpression.escapedPattern(for: String(key[start..<range.lowerBound])) + "(.*?)"
                start = range.upperBound
            }
            pattern += NSRegularExpression.escapedPattern(for: String(key[start...]))
            guard let regex = try? NSRegularExpression(pattern: "^" + pattern + "$", options: .dotMatchesLineSeparators),
                  let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { continue }
            let arguments = (1..<match.numberOfRanges).map {
                Range(match.range(at: $0), in: text).map { String(text[$0]) } ?? ""
            }
            return render(key, arguments: arguments, language: .simplifiedChinese)
        }
        return text
    }

    static func number(_ value: Int) -> String { value.formatted(.number.locale(locale)) }
    static func date(_ value: Date) -> String {
        value.formatted(.dateTime.year().month(.abbreviated).day().locale(locale))
    }
    static func time(_ value: Date) -> String {
        value.formatted(.dateTime.hour().minute().locale(locale))
    }

    /// The caller removes the observer in the panel's completion handler.
    static func localize(_ panel: NSOpenPanel, title: Message = "Choose Folders…",
                         prompt: Message, message: Message) -> NSObjectProtocol {
        let refresh: @MainActor @Sendable () -> Void = { [weak panel] in
            panel?.title = tr(title)
            panel?.prompt = tr(prompt)
            panel?.message = tr(message)
        }
        refresh()
        return NotificationCenter.default.addObserver(forName: didChange, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { refresh() }
        }
    }
}

/// Locale changes redraw controls without replacing the root or resetting its state.
struct LocalizedRoot<Content: View>: View {
    let content: Content
    var body: some View {
        content.environment(\.locale, L10n.locale)
    }
}

struct LanguagePicker: View {
    var body: some View {
        Picker(L10n.tr("Language"), selection: Binding(
            get: { LanguageSettings.shared.language },
            set: { LanguageSettings.shared.language = $0 }
        )) {
            ForEach(AppLanguage.allCases) { language in
                Text(verbatim: language.name).tag(language)
            }
        }
    }
}

extension ModelDownload {
    var localizedProgressText: String {
        switch phase {
        case .needed: return bundled ? L10n.tr("Bundled model") : L10n.tr("Not downloaded yet")
        case .downloading:
            let percent = "\(Int((fraction * 100).rounded(.down)))%"
            guard let left = secondsLeft else { return percent }
            return percent + " · " + (left < 50 ? L10n.tr("less than a minute left")
                : L10n.tr("about \(roughDuration(left)) left"))
        case .verifying: return bundled ? L10n.tr("Verifying bundled models…") : L10n.tr("Checking the download…")
        case .retrying: return L10n.tr("Waiting for the network…")
        case .failed(let message): return L10n.resolve(message)
        case .ready: return L10n.tr("Ready")
        }
    }
}
