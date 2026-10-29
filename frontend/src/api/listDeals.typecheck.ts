import type { listDeals } from "@/api/client";

type Deal = Awaited<ReturnType<typeof listDeals>>[number];
type Equal<Left, Right> =
  (<T>() => T extends Left ? 1 : 2) extends
  (<T>() => T extends Right ? 1 : 2) ? true : false;
type Assert<Condition extends true> = Condition;

type _IdIsString = Assert<Equal<Deal["Id"], string>>;
type _NameIsNullable = Assert<Equal<Deal["Name"], string | null>>;
type _StageNameIsNullable = Assert<Equal<Deal["StageName"], string | null>>;
type _CloseDateIsNullable = Assert<Equal<Deal["CloseDate"], string | null>>;
type _AccountNameIsNullable = Assert<Equal<Deal["AccountName"], string | null>>;
