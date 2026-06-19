function Bone({ className = "" }) {
    return (
        <div className={`bg-slate-200 animate-pulse rounded ${className}`} />
    );
}

const BAR_HEIGHTS = [45, 72, 58, 85, 38, 65, 90, 52, 78, 42, 68, 55];

export function CardSkeleton() {
    return (
        <div className="bg-white rounded-lg border border-slate-200 p-5 flex items-start gap-4">
            <Bone className="w-10 h-10 rounded-lg shrink-0" />
            <div className="flex-1 space-y-2 pt-0.5">
                <Bone className="h-3 w-20" />
                <Bone className="h-7 w-28" />
            </div>
        </div>
    );
}

export function ChartSkeleton() {
    return (
        <div className="bg-white rounded-lg border border-slate-200">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center gap-2">
                <Bone className="w-4 h-4 rounded" />
                <Bone className="h-4 w-40" />
            </div>
            <div className="p-5 space-y-4">
                <div className="flex items-end gap-3 h-48">
                    {BAR_HEIGHTS.map((h, i) => (
                        <Bone
                            key={i}
                            className="flex-1"
                            style={{ height: `${h}%` }}
                        />
                    ))}
                </div>
                <div className="flex justify-center gap-6">
                    <Bone className="h-3 w-16" />
                    <Bone className="h-3 w-16" />
                    <Bone className="h-3 w-16" />
                </div>
            </div>
        </div>
    );
}

export function TableSkeleton({ rows = 5, cols = 4 }) {
    return (
        <div className="bg-white rounded-lg border border-slate-200">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center gap-2">
                <Bone className="w-4 h-4 rounded" />
                <Bone className="h-4 w-32" />
            </div>
            <div className="divide-y divide-slate-100">
                {/* Header row */}
                <div className="flex gap-4 px-5 py-3 bg-slate-50">
                    {Array.from({ length: cols }).map((_, i) => (
                        <Bone key={i} className="h-3 flex-1 max-w-24" />
                    ))}
                </div>
                {/* Body rows */}
                {Array.from({ length: rows }).map((_, r) => (
                    <div key={r} className="flex gap-4 px-5 py-3">
                        {Array.from({ length: cols }).map((_, c) => (
                            <Bone key={c} className="h-3.5 flex-1 max-w-24" />
                        ))}
                    </div>
                ))}
            </div>
        </div>
    );
}

export function OverviewSkeleton() {
    return (
        <div className="px-8 py-6 space-y-6 bg-gray-100 rounded-3xl">
            {/* Header with date pickers */}
            <div className="flex items-center justify-between">
                <div className="space-y-2">
                    <Bone className="h-6 w-36" />
                    <Bone className="h-3 w-64" />
                </div>
                <div className="flex items-center gap-2">
                    <Bone className="h-7 w-32 rounded-md" />
                    <Bone className="h-3 w-4" />
                    <Bone className="h-7 w-32 rounded-md" />
                    <Bone className="h-5 w-16 rounded-xl" />
                </div>
            </div>

            {/* Info banner */}
            <Bone className="h-10 w-full rounded-lg" />

            {/* 6 KPI cards in 3-col grid */}
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                {Array.from({ length: 6 }).map((_, i) => (
                    <CardSkeleton key={i} />
                ))}
            </div>

            {/* Tab bar */}
            <div className="rounded-xl border border-slate-200 bg-white px-2 py-3 flex gap-2">
                {Array.from({ length: 4 }).map((_, i) => (
                    <Bone key={i} className="h-4 flex-1 max-w-28 mx-auto" />
                ))}
            </div>

            {/* Dual charts side-by-side */}
            <div className="flex items-stretch gap-4">
                <div className="flex-1 min-w-0">
                    <ChartSkeleton />
                </div>
                <div className="flex-1 min-w-0">
                    <ChartSkeleton />
                </div>
            </div>

            {/* Table */}
            <TableSkeleton rows={4} cols={6} />
        </div>
    );
}

export function DetailSkeleton() {
    return (
        <div className="px-8 py-6 space-y-6 bg-gray-100 rounded-3xl">
            {/* Header with date range */}
            <div className="flex items-center justify-between">
                <div className="space-y-2">
                    <Bone className="h-6 w-28" />
                    <Bone className="h-3 w-72" />
                </div>
                <div className="flex items-center gap-2">
                    <Bone className="h-7 w-32 rounded-md" />
                    <Bone className="h-3 w-4" />
                    <Bone className="h-7 w-32 rounded-md" />
                </div>
            </div>

            {/* KPI cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {Array.from({ length: 3 }).map((_, i) => (
                    <CardSkeleton key={i} />
                ))}
            </div>

            {/* Chart */}
            <ChartSkeleton />

            {/* Table */}
            <TableSkeleton rows={4} cols={4} />
        </div>
    );
}
