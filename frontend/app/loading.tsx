export default function Loading() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="skeleton h-8 w-48 rounded" />
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="card p-5">
            <div className="skeleton h-3 w-24 mb-3 rounded" />
            <div className="skeleton h-8 w-32 rounded" />
            <div className="skeleton h-3 w-16 mt-2 rounded" />
          </div>
        ))}
      </div>
      <div className="card p-5">
        <div className="skeleton h-4 w-36 mb-4 rounded" />
        <div className="space-y-3">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="skeleton h-10 rounded" />
          ))}
        </div>
      </div>
    </div>
  );
}
