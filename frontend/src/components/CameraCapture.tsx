import { useEffect, useRef, useState } from 'react';

import { AlertIcon } from './Icons';

interface CameraCaptureProps {
  onCapture: (file: File) => void;
}

/**
 * Live camera preview → capture a still → confirm/retake, then hand the caller a File.
 *
 * `facingMode: 'environment'` prefers a phone's rear camera; desktop browsers with a
 * single webcam ignore it and use whatever they have.
 */
export function CameraCapture({ onCapture }: CameraCaptureProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [stillBlob, setStillBlob] = useState<Blob | null>(null);
  const [stillUrl, setStillUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const startStream = () => {
    navigator.mediaDevices
      ?.getUserMedia({ video: { facingMode: 'environment' } })
      .then((stream) => {
        streamRef.current = stream;
        if (videoRef.current) videoRef.current.srcObject = stream;
      })
      .catch(() => setError('Camera unavailable — use the file picker instead.'));
  };

  useEffect(() => {
    startStream();
    // Stops the camera light whether the user leaves this view or the component unmounts
    // for any other reason — a dangling MediaStream keeps recording after the UI is gone.
    return () => {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, []);

  const capture = () => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d')?.drawImage(video, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        setStillBlob(blob);
        setStillUrl(URL.createObjectURL(blob));
      },
      'image/jpeg',
      0.92,
    );
  };

  const retake = () => {
    if (stillUrl) URL.revokeObjectURL(stillUrl);
    setStillBlob(null);
    setStillUrl(null);
    setError(null);
    startStream();
  };

  const usePhoto = () => {
    if (!stillBlob) return;
    onCapture(new File([stillBlob], `camera-${Date.now()}.jpg`, { type: 'image/jpeg' }));
    if (stillUrl) URL.revokeObjectURL(stillUrl);
  };

  if (error) {
    return (
      <p className="error">
        <AlertIcon size={15} /> {error}
      </p>
    );
  }

  if (stillUrl) {
    return (
      <div className="camera-capture">
        <img src={stillUrl} alt="Captured document" className="camera-still" />
        <div className="actions">
          <button type="button" className="secondary" onClick={retake}>
            Retake
          </button>
          <button type="button" onClick={usePhoto}>
            Use photo
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="camera-capture">
      <video ref={videoRef} autoPlay playsInline muted className="camera-video" />
      <div className="actions">
        <button type="button" onClick={capture}>
          Capture
        </button>
      </div>
    </div>
  );
}
