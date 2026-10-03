export function upload(file: Blob) {
  return fetch("/upload", { method: "POST", body: file });
}
