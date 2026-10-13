// Apple Vision person segmentation → raw 8-bit matte frames on stdout (pipe into ffmpeg alphamerge)
// usage: vision-matte <clip.mp4> [accurate|balanced|fast] | ffmpeg -f rawvideo -pix_fmt gray -s WxH -r FPS -i - ...
//        vision-matte <photo.png|jpg|webp> [accurate|…]  → one matte frame (W×H bytes) for a still photo
// engine/cutout compiles this once with `swiftc -O` into <TAD_CLIP_HOME>/cache/vision-matte (again whenever this file is newer)
// VNSequenceRequestHandler + one reused request = Vision's temporal smoothing across frames (meant for video)
import AVFoundation
import CoreImage
import Foundation
import Vision

let args = CommandLine.arguments
let url = URL(fileURLWithPath: args[1])
let level = args.count > 2 ? args[2] : "accurate"
let ctx = CIContext(options: [.workingColorSpace: NSNull()])

func makeRequest() -> VNGeneratePersonSegmentationRequest {
  let r = VNGeneratePersonSegmentationRequest()
  r.qualityLevel = level == "fast" ? .fast : level == "balanced" ? .balanced : .accurate
  r.outputPixelFormat = kCVPixelFormatType_OneComponent8
  return r
}

// the mask comes back smaller than the picture → scale it to W×H and render 8-bit gray into `bitmap`
func renderMask(_ request: VNGeneratePersonSegmentationRequest, _ W: Int, _ H: Int, _ bitmap: inout [UInt8]) {
  let mask = CIImage(cvPixelBuffer: request.results!.first!.pixelBuffer)
  let scaled = mask.applyingFilter("CILanczosScaleTransform", parameters: [
    kCIInputScaleKey: CGFloat(H) / mask.extent.height,
    kCIInputAspectRatioKey: (CGFloat(W) / mask.extent.width) / (CGFloat(H) / mask.extent.height),
  ])
  ctx.render(scaled, toBitmap: &bitmap, rowBytes: W, bounds: CGRect(x: 0, y: 0, width: W, height: H), format: .L8, colorSpace: nil)
}

// still photo (engine/cutout hands over an upright RGB PNG, EXIF rotation already applied)
if ["png", "jpg", "jpeg", "webp", "heic", "tif", "tiff"].contains(url.pathExtension.lowercased()) {
  guard let img = CIImage(contentsOf: url) else {
    FileHandle.standardError.write("vision: cannot read image \(args[1])\n".data(using: .utf8)!)
    exit(1)
  }
  let W = Int(img.extent.width), H = Int(img.extent.height)
  let request = makeRequest()
  try! VNImageRequestHandler(ciImage: img, options: [:]).perform([request])
  var bitmap = [UInt8](repeating: 0, count: W * H)
  renderMask(request, W, H, &bitmap)
  FileHandle.standardOutput.write(Data(bitmap))
  FileHandle.standardError.write("vision \(level): still \(W)x\(H)\n".data(using: .utf8)!)
  exit(0)
}

let asset = AVURLAsset(url: url)
let track = asset.tracks(withMediaType: .video)[0]
let W = Int(track.naturalSize.width), H = Int(track.naturalSize.height)

let reader = try! AVAssetReader(asset: asset)
let output = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(output)
reader.startReading()

let request = makeRequest()
let sequence = VNSequenceRequestHandler()
var bitmap = [UInt8](repeating: 0, count: W * H)
let start = Date()
var frames = 0
var heldEmpty = false

while let sample = output.copyNextSampleBuffer() {
  guard let frame = CMSampleBufferGetImageBuffer(sample) else { continue }
  try! sequence.perform([request], on: frame)
  renderMask(request, W, H, &bitmap)
  // Vision returns an empty matte for the very first frame of a sequence (running it twice does not help) → every cut-out
  // shot opened with one frame without the speaker · hold an empty frame 0 and write frame 1's matte in its place
  if frames == 0 && !bitmap.contains(where: { $0 > 32 }) { heldEmpty = true; frames += 1; continue }
  if heldEmpty { FileHandle.standardOutput.write(Data(bitmap)); heldEmpty = false }
  FileHandle.standardOutput.write(Data(bitmap))
  frames += 1
}
if heldEmpty { FileHandle.standardOutput.write(Data(bitmap)) }   // one-frame clip: still emit a frame
FileHandle.standardError.write("vision \(level): \(frames) frames \(W)x\(H) in \(String(format: "%.1f", Date().timeIntervalSince(start)))s\n".data(using: .utf8)!)
