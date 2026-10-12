// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "GastroAPI",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [
        .library(name: "GastroAPI", targets: ["GastroAPI"]),
        .executable(name: "GastroSmoke", targets: ["GastroSmoke"])
    ],
    targets: [
        .target(name: "GastroAPI"),
        .executableTarget(name: "GastroSmoke", dependencies: ["GastroAPI"]),
        .testTarget(name: "GastroAPITests", dependencies: ["GastroAPI"])
    ]
)
