import Link from "next/link";

export default function NotFound() {
  return <section className="page"><div className="shell">
    <p className="eyebrow">404</p><h1 style={{ fontSize: "clamp(36px,4.4vw,60px)", margin: "14px 0" }}>Page not found.</h1>
    <p className="muted">The page you requested does not exist.</p>
    <Link className="button button-blue" href="/">Return home</Link>
  </div></section>;
}
