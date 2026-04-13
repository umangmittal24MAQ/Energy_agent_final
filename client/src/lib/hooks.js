import { useQuery } from "@tanstack/react-query";
import { fetchKpis, fetchUnifiedData } from "./api";

export function useKpis() {
    return useQuery({
        queryKey: ["kpis"],
        queryFn: fetchKpis,
    });
}

export function useUnifiedData() {
    return useQuery({
        queryKey: ["unified-data"],
        queryFn: fetchUnifiedData,
    });
}
