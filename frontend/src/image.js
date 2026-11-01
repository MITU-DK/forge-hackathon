// Crop photo -> JPEG <= 1024 px on the long side, as raw base64 (the API wants no "data:" prefix).
const MAX = 1024;

export async function prepareImage(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = () => reject(new Error("unreadable image"));
      i.src = url;
    });
    const scale = Math.min(1, MAX / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.8);
    return { preview: dataUrl, b64: dataUrl.slice(dataUrl.indexOf(",") + 1) };
  } finally {
    URL.revokeObjectURL(url);
  }
}
