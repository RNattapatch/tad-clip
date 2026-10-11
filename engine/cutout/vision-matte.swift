// Apple Vision person segmentation → raw 8-bit matte frames on stdout (pipe into ffmpeg alphamerge)
// usage: vision-matte <clip.mp4> [accurate|balanced|fast] | ffmpeg -f rawvideo -pix_fmt gray -s WxH -r FPS -i - ...
// engine/cutout compiles this once with `swiftc -O` into <TAD_CLIP_HOME>/cache/vision-matte (again whenever this file is newer)
// VNSequenceRequestHandler + one reused request = Vision's temporal smoothing across frames (meant for video)
import AVFoundation
import CoreImage
import Foundation
import Vision

let args = CommandLine.arguments
let asset = AVURLAsset(url: URL(fileURLWithPath: args[1]))
let level = args.count > 2 ? args[2] : "accurate"
let track = asset.tracks(withMediaType: .video)[0]
let W = Int(track.naturalSize.width), H = Int(track.naturalSize.height)

let reader = try! AVAssetReader(asset: asset)
let output = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(output)
reader.startReading()

let request = VNGeneratePersonSegmentationRequest()
request.qualityLevel = level == "fast" ? .fast : level == "balanced" ? .balanced : .accurate
request.outputPixelFormat = kCVPixelFormatType_OneComponent8
let sequence = VNSequenceRequestHandler()
let ctx = CIContext(options: [.workingColorSpace: NSNull()])
var bitmap = [UInt8](repeating: 0, count: W * H)
let start = Date()
var frames = 0

while let sample = output.copyNextSampleBuffer() {
  guard let frame = CMSampleBufferGetImageBuffer(sample) else { continue }
  try! sequence.perform([request], on: frame)
  let mask = CIImage(cvPixelBuffer: request.results!.first!.pixelBuffer)
  let scaled = mask.applyingFilter("CILanczosScaleTransform", parameters: [
    kCIInputScaleKey: CGFloat(H) / mask.extent.height,
    kCIInputAspectRatioKey: (CGFloat(W) / mask.extent.width) / (CGFloat(H) / mask.extent.height),
  ])
  ctx.render(scaled, toBitmap: &bitmap, rowBytes: W, bounds: CGRect(x: 0, y: 0, width: W, height: H), format: .L8, colorSpace: nil)
  FileHandle.standardOutput.write(Data(bitmap))
  frames += 1
}
FileHandle.standardError.write("vision \(level): \(frames) frames \(W)x\(H) in \(String(format: "%.1f", Date().timeIntervalSince(start)))s\n".data(using: .utf8)!)
