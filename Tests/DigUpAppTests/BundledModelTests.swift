import DigUpKit
import Foundation
import Testing
@testable import DigUpApp

@Suite @MainActor
struct BundledModelTests {
    @Test func missingPackagedModelsFailWithoutCreatingDownloadFiles() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let download = ModelDownload(folder: folder, bundled: true)
        #expect(!download.isReady)
        download.start()
        for _ in 0..<200 {
            if case .failed = download.phase { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        guard case .failed(let message) = download.phase else {
            Issue.record("Bundled verification did not reject missing models")
            return
        }
        #expect(message.contains("Repair the installation"))
        #expect(!FileManager.default.fileExists(atPath: folder.path))
        #expect(!download.isActive)
        download.use([])
        #expect(!FileManager.default.fileExists(atPath: folder.path))
    }

    @Test func existingButCorruptModelsAreNotConsideredReady() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        for file in ModelFiles.all {
            try Data("broken".utf8).write(to: folder.appendingPathComponent(file.name))
        }
        let download = ModelDownload(folder: folder, bundled: true)
        #expect(!download.isReady)
        download.start()
        for _ in 0..<200 {
            if case .failed = download.phase { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(!download.isReady)
        #expect(!download.isActive)
        #expect(try FileManager.default.contentsOfDirectory(atPath: folder.path).count == 2)
    }

    @Test func manualUpdatesCannotEnableAutomaticInstallation() {
        let settings = AppSettings.load()
        let model = AppModel(settings: settings, download: ModelDownload(folder: settings.modelsDirectory))
        let updates = Updates(model: model)
        updates.automaticallyChecks = true
        #expect(!updates.automaticallyChecks)
    }
}
