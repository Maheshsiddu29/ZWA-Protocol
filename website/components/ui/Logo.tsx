import Image from "next/image";

/** Official outlined lockups supplied in design-assets/brand. */
export function Logo({ className = "", inverse = false }: { className?: string; inverse?: boolean; withProtocol?: boolean }) {
  return <Image src={inverse ? "/brand/zwa-logo-dark.svg" : "/brand/zwa-logo-light.svg"} alt="ZWA Protocol" width={216} height={54} className={className} priority />;
}
