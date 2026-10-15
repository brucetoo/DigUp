import AppKit
import Foundation
import Observation
import os
import Testing
@testable import DigUpApp
@testable import DigUpKit

@Suite(.serialized) @MainActor
struct LocalizationTests {
    private func isolated(_ body: (LanguageSettings, UserDefaults) throws -> Void) rethrows {
        let suite = "DigUp.localization.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        let previous = LanguageSettings.shared
        let settings = LanguageSettings(defaults: defaults)
        LanguageSettings.shared = settings
        defer {
            LanguageSettings.shared = previous
            defaults.removePersistentDomain(forName: suite)
        }
        try body(settings, defaults)
    }

    @Test func defaultsToChineseAndPersistsEnglish() {
        isolated { settings, defaults in
            #expect(settings.language == .simplifiedChinese)
            #expect(L10n.tr("Settings…") == "设置…")
            settings.language = .english
            #expect(defaults.string(forKey: LanguageSettings.defaultsKey) == "en")
            #expect(LanguageSettings(defaults: defaults).language == .english)
            #expect(L10n.tr("Settings…") == "Settings…")
            settings.language = .simplifiedChinese
            #expect(LanguageSettings(defaults: defaults).language == .simplifiedChinese)
            defaults.set("unsupported", forKey: LanguageSettings.defaultsKey)
            #expect(LanguageSettings(defaults: defaults).language == .simplifiedChinese)
        }
    }

    @Test func changeIsObservableAndNotificationIsImmediate() {
        isolated { settings, _ in
            let observed = OSAllocatedUnfairLock(initialState: false)
            let notified = OSAllocatedUnfairLock(initialState: false)
            withObservationTracking {
                _ = L10n.tr("Search")
            } onChange: {
                observed.withLock { $0 = true }
            }
            let token = NotificationCenter.default.addObserver(forName: L10n.didChange, object: settings,
                                                               queue: nil) { _ in
                notified.withLock { $0 = true }
            }
            defer { NotificationCenter.default.removeObserver(token) }
            settings.language = .english
            #expect(observed.withLock { $0 })
            #expect(notified.withLock { $0 })
            #expect(L10n.tr("Search") == "Search")
        }
    }

    @Test func interpolationPreservesUserValuesAndReordersSafely() {
        isolated { settings, _ in
            let filename = "receipt {1} 中文.swift"
            let message: L10n.Message = "The chosen \(filename) is \("2 MB"), not \("3 MB")"
            #expect(L10n.tr(message) == "所选 receipt {1} 中文.swift 的大小是 2 MB，应为 3 MB")
            settings.language = .english
            #expect(L10n.tr(message) == "The chosen receipt {1} 中文.swift is 2 MB, not 3 MB")
            #expect(L10n.tr("Unknown \(filename)") == "Unknown receipt {1} 中文.swift")
            settings.language = .simplifiedChinese
            #expect(L10n.tr("Unknown \(filename)") == "Unknown receipt {1} 中文.swift")
        }
    }

    @Test func catalogHasValidArgumentReferences() throws {
        #expect(L10n.catalog.count > 350)
        let token = try NSRegularExpression(pattern: #"\{\d+\}"#)
        func tokens(_ text: String) -> Set<String> {
            Set(token.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap {
                Range($0.range, in: text).map { String(text[$0]) }
            })
        }
        for (key, translation) in L10n.catalog {
            #expect(!translation.isEmpty)
            #expect(tokens(translation).isSubset(of: tokens(key)), "Invalid arguments in \(key)")
        }
    }

    @Test func dynamicStatusAndErrorsRefreshWithoutAnEngineEvent() {
        isolated { language, _ in
            var settings = AppSettings.load()
            settings.roots = [URL(fileURLWithPath: "/tmp/DigUpLocalization")]
            let model = AppModel(settings: settings, download: ModelDownload(
                folder: URL(fileURLWithPath: "/tmp/DigUpLocalizationMissingModel-\(UUID().uuidString)")))
            model.status.activity = .indexing(done: 2, total: 4)
            #expect(model.statusLine == "正在索引 50% · 4 个中已完成 2 个")
            language.language = .english
            #expect(model.statusLine == "Indexing 50% · 2 of 4")
            model.status.activity = .failed("Can't open the index")
            #expect(model.statusLine.contains("Can't open the index"))
            language.language = .simplifiedChinese
            #expect(model.statusLine.contains("无法打开索引"))
            #expect(model.download.localizedProgressText == "尚未下载")
            #expect(L10n.resolve("Another app is using ⇧⌘Space") == "其他应用正在使用 ⇧⌘Space")
            #expect(L10n.resolve("on battery") == "正在使用电池")
        }
    }

    @Test func countsTabsDurationsAndResultMetadataRefresh() {
        isolated { language, _ in
            let hit = SearchHit(path: "/tmp/receipt {1}.pdf", kind: .pdf, score: 1, z: 3, cosine: nil,
                                keywordRank: 1, segment: .page, segmentID: 1, loc: 3, locEnd: nil,
                                excerpt: "receipt unchanged", modified: 0, size: 0, info: ["pages": "4"])
            let row = ResultRow(hit, terms: ["receipt"])
            #expect(row.place == "第 3 页")
            #expect(row.reason == "第 3 页的文字匹配")
            #expect(row.details.contains("4 页"))
            #expect(FileKind.screenshot.counted(2) == "2 张截图")
            #expect(SettingsTab.general.title == "通用")
            #expect(roughDuration(120) == "2 分钟")
            #expect(CodeEditor.defaultApp.displayName == "文件的默认应用")
            language.language = .english
            #expect(row.place == "p. 3")
            #expect(row.reason == "Matches the text on page 3")
            #expect(FileKind.screenshot.counted(1) == "1 screenshot")
            #expect(FileKind.screenshot.counted(2) == "2 screenshots")
            #expect(SettingsTab.general.title == "General")
            #expect(roughDuration(120) == "2 min")
            #expect(row.name == "receipt {1}.pdf")
            #expect(row.excerpt == "receipt unchanged")
            #expect(row.terms == ["receipt"])
        }
    }

    @Test func storedFolderNoticeRefreshes() {
        isolated { language, _ in
            let root = URL(fileURLWithPath: "/tmp/DigUpLocalization")
            let choices = FolderChoices(rows: [], selection: FolderSelection(kept: [root.path]), types: [])
            choices.add([root])
            #expect(choices.note != nil)
            #expect(L10n.tr(choices.note!).contains("已在搜索范围内"))
            language.language = .english
            #expect(L10n.tr(choices.note!).contains("is searched already"))
        }
    }

    @Test func openPanelRefreshesWithoutRecreation() {
        isolated { language, _ in
            _ = NSApplication.shared
            let panel = NSOpenPanel()
            let observer = L10n.localize(panel, prompt: "Add", message: "Choose folders for DigUp to search.")
            defer { NotificationCenter.default.removeObserver(observer) }
            #expect(panel.prompt == "添加")
            #expect(panel.message == "选择要让 DigUp 搜索的文件夹。")
            language.language = .english
            #expect(panel.prompt == "Add")
            #expect(panel.message == "Choose folders for DigUp to search.")
        }
    }
}
