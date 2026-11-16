// MAJOR.MINOR.PATCH, compared as numbers (1.10.0 is newer than 1.9.0). Anything else sorts as 0.0.0.
export function compareVersions(a: string, b: string): -1 | 0 | 1 {
  const parse = (v: string) => (/^\d+\.\d+\.\d+$/.test(v) ? v.split(".").map(Number) : [0, 0, 0]);
  const [x, y] = [parse(a), parse(b)];
  for (let i = 0; i < 3; i += 1) {
    if (x[i] !== y[i]) return x[i] > y[i] ? 1 : -1;
  }
  return 0;
}
