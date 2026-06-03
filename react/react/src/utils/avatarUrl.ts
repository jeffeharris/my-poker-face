// Repoint an avatar URL at a different per-emotion image. The backend serves
// `/api/avatar/{name}/{emotion}` (with 404→fallback), so swapping the emotion
// segment is enough to change the face — the same trick the "thinking" highlight
// uses. Leaves a non-matching/empty URL untouched.
//
// Accepts `undefined` and returns it unchanged so call sites can pass an
// optional `avatar_url` (string | undefined) without extra guarding — matching
// the behavior of the original inline/local implementations.
export function avatarUrlForEmotion(url: string | undefined, emotion: string): string | undefined {
  if (!url) return url;
  return url.replace(/\/api\/avatar\/(.+?)\/[^/]+(\/full)?$/, `/api/avatar/$1/${emotion}$2`);
}
