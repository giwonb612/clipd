import os
import tempfile


def extract_text_from_image(image_bytes: bytes) -> str:
    """Use Apple Vision framework to extract text from image bytes.
    Supports Korean + English. Runs fully locally."""
    try:
        import Vision
        from Foundation import NSURL

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name

        try:
            url = NSURL.fileURLWithPath_(tmp_path)
            handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)

            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLanguages_(["ko-KR", "en-US"])
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            request.setUsesLanguageCorrection_(True)

            handler.performRequests_error_([request], None)

            results = request.results()
            if not results:
                return ""

            lines = []
            for obs in results:
                candidates = obs.topCandidates_(1)
                if candidates and len(candidates) > 0:
                    lines.append(str(candidates[0].string()))

            return "\n".join(lines)
        finally:
            os.unlink(tmp_path)

    except Exception:
        return ""
