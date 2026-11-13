// Person freistellen und Gesicht finden mit Apples Vision-Framework (nur macOS, ohne Download).
// Aufruf: freistellen <bild> <maske.png>  ->  schreibt die Personen-Maske (Graustufen, Bildgröße)
// und gibt JSON aus: {"gesicht": [x, y, b, h]} in Anteilen, Ursprung oben links (leer, wenn keins gefunden).
import Foundation
import Vision
import CoreImage

let args = CommandLine.arguments
guard args.count >= 3, let bild = CIImage(contentsOf: URL(fileURLWithPath: args[1]), options: [.applyOrientationProperty: true]) else {
    FileHandle.standardError.write("Bild nicht lesbar\n".data(using: .utf8)!); exit(1)
}
let handler = VNImageRequestHandler(ciImage: bild)
let person = VNGeneratePersonSegmentationRequest()
person.qualityLevel = .accurate
person.outputPixelFormat = kCVPixelFormatType_OneComponent8
let gesicht = VNDetectFaceRectanglesRequest()
do { try handler.perform([person, gesicht]) } catch { FileHandle.standardError.write("\(error)\n".data(using: .utf8)!); exit(2) }

var g: [Double] = []
if let f = gesicht.results?.max(by: { $0.boundingBox.width < $1.boundingBox.width }) {
    let b = f.boundingBox
    g = [Double(b.minX), Double(1 - b.maxY), Double(b.width), Double(b.height)]
}
if let maske = person.results?.first?.pixelBuffer {
    var m = CIImage(cvPixelBuffer: maske)
    m = m.transformed(by: CGAffineTransform(scaleX: bild.extent.width / m.extent.width, y: bild.extent.height / m.extent.height))
    let ctx = CIContext()
    try? ctx.writePNGRepresentation(of: m, to: URL(fileURLWithPath: args[2]), format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
}
print("{\"gesicht\": \(g)}")
