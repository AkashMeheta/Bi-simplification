# =============================================================================
# STEP 2 — Helpers
# =============================================================================

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope

DEFAULT_TABLE = "SUBQUERIES_CTES"  # placeholder for columns/tables we can't
                                    # confidently tie to one real base table


def classify(username: str) -> str:
    return "app" if username.lower().startswith(APP_PREFIXES) else "user"


def _strip_locking_prefix(text: str) -> str:
    """
    Teradata locking-request modifiers (e.g. LOCKING ROW FOR ACCESS) are
    valid Teradata syntax at the TOP level of a statement, but sqlglot can't
    parse them when they appear nested inside a CREATE ... AS (...) body —
    that nesting is what causes the whole CREATE to fall back to a generic,
    unparseable "Command" node. Since the clause is just a lock hint (it
    doesn't affect which tables/columns are referenced), we strip it before
    re-parsing the inner query on its own.
    """
    stripped = text.lstrip()
    if re.match(r'(?i)^locking\b', stripped):
        m = re.search(r'(?i)\b(select|with)\b', stripped)
        if m:
            return stripped[m.start():]
    return text


def _extract_inner_query(sql_text: str):
    """
    Pull the SELECT/WITH body out of a CREATE ... AS (...) / CREATE ... AS
    SELECT ... statement so it can be parsed on its own, independent of the
    (possibly-unsupported-by-sqlglot) outer CREATE syntax.
    """
    m = re.search(r'\bAS\b\s*\(', sql_text, flags=re.IGNORECASE)
    if m:
        start = m.end() - 1  # index of '('
        depth = 0
        for i in range(start, len(sql_text)):
            if sql_text[i] == '(':
                depth += 1
            elif sql_text[i] == ')':
                depth -= 1
                if depth == 0:
                    return _strip_locking_prefix(sql_text[start + 1:i].strip())
        return None  # unbalanced parens — give up

    m = re.search(r'\bAS\b\s*(SELECT\b.*)', sql_text, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return _strip_locking_prefix(m.group(1).strip())
    return None


def _walk_top_level(n):
    """
    Yield ('table'|'column'|'select', node) for parts of n that belong to
    n's OWN top-level clauses — stops descending the moment it hits a
    nested exp.Select (that subquery is its own separate scope and gets
    handed off to the normal scope-based path separately). Used only for
    UPDATE / MERGE, whose scope sqlglot's build_scope doesn't reliably
    represent (see _extract_from_node).
    """
    if isinstance(n, exp.Select):
        yield ('select', n)
        return
    if isinstance(n, exp.Table):
        yield ('table', n)
        # do NOT return here — Teradata comma-joined FROM lists
        # ("FROM t1, t2") are parsed with t2 nested INSIDE t1's own
        # 'joins' arg, not as a sibling table. Falling through to the
        # generic per-arg recursion below picks up n.args['joins'] (and
        # any chain of further comma/JOIN-ed tables nested within those)
        # so no joined table gets silently missed.
    elif isinstance(n, exp.Column):
        yield ('column', n)
        return
    for child in n.args.values():
        if isinstance(child, list):
            for c in child:
                if isinstance(c, exp.Expression):
                    yield from _walk_top_level(c)
        elif isinstance(child, exp.Expression):
            yield from _walk_top_level(child)


def _set_target_columns(node):
    """
    Identify the columns on the LEFT side of a SET assignment in an
    UPDATE (either standalone, or nested inside a MERGE's WHEN MATCHED
    THEN UPDATE clause) — these always belong to the statement's own
    target table by SQL semantics, even when unqualified and even when
    other tables are joined in via FROM/USING (e.g. plain "SET col = ..."
    is never ambiguous about which table `col` belongs to, regardless of
    how many tables are joined in the FROM). Returns (target_name,
    set of id(column_node) for each SET-clause LHS column).
    """
    target_table = None
    set_exprs = []

    if isinstance(node, exp.Update):
        target_table = node.this
        set_exprs = node.args.get('expressions', []) or []
    elif isinstance(node, exp.Merge):
        target_table = node.this
        for when in node.find_all(exp.When):
            then = when.args.get('then')
            if isinstance(then, exp.Update):
                set_exprs.extend(then.args.get('expressions', []) or [])

    target_name = None
    if isinstance(target_table, exp.Table) and target_table.name:
        target_name = target_table.name.upper()

    lhs_ids = set()
    for eq in set_exprs:
        if isinstance(eq, exp.EQ) and isinstance(eq.this, exp.Column):
            lhs_ids.add(id(eq.this))

    return target_name, lhs_ids


def _extract_from_unscopable_dml(node):
    """
    Manual alias resolution for UPDATE / MERGE — same resolution rules as
    the scope-based path (qualified columns resolve via this statement's
    own table aliases; unqualified columns resolve only when exactly one
    table is in play; anything ambiguous or unresolved goes to
    DEFAULT_TABLE instead of being guessed at), PLUS a special case for
    SET-clause target columns, which always belong to the statement's own
    target table regardless of how many other tables are joined in.
    Nested subqueries (EXISTS(...), scalar subqueries in SET) are
    scopable on their own and handed off to _extract_from_node
    recursively rather than being blended into the outer alias map.
    """
    alias_to_table = {}
    columns = []
    nested_selects = []

    for kind, n in _walk_top_level(node):
        if kind == 'table' and n.name:
            alias_to_table[(n.alias or n.name).upper()] = n.name.upper()
        elif kind == 'column' and n.name:
            columns.append(n)
        elif kind == 'select':
            nested_selects.append(n)

    target_name, set_lhs_ids = _set_target_columns(node)

    scope_tables = list(dict.fromkeys(alias_to_table.values()))
    pairs = []
    seen = set()
    for col in columns:
        col_name = col.name.upper()

        if id(col) in set_lhs_ids and target_name:
            key = (target_name, col_name)
        else:
            tbl_ref = col.table.upper() if col.table else None
            if tbl_ref:
                key = (alias_to_table.get(tbl_ref, DEFAULT_TABLE), col_name)
            elif len(scope_tables) == 1:
                key = (scope_tables[0], col_name)
            else:
                key = (DEFAULT_TABLE, col_name)

        if key not in seen:
            seen.add(key)
            pairs.append(key)

    for sel in nested_selects:
        pairs.extend(_extract_from_node(sel))

    return pairs


def _extract_from_node(node) -> list:
    """
    Pull (table, column) pairs out of a parsed sqlglot node/subtree.

    UPDATE / MERGE are ALWAYS routed to _extract_from_unscopable_dml,
    regardless of what build_scope returns for them — sqlglot's scope
    builder is unreliable for these two statement types specifically:
    it returns None outright for some shapes (Teradata UPDATE...FROM
    comma/JOIN syntax), and for OTHER shapes (e.g. an UPDATE whose only
    tables live inside nested subqueries in SET/WHERE, with no top-level
    FROM) it returns a Scope object that does NOT correctly represent
    the statement — the SET-target column and other top-level columns
    get silently skipped rather than resolved. Checking "is build_scope's
    result None" is not a reliable signal for these two types, so they
    never go through the scope-based path at all.

    Everything else (SELECT, INSERT...SELECT, DELETE, CREATE...AS
    SELECT) uses scope-based alias resolution:
      - Each SELECT/JOIN/subquery/CTE is its own scope, so columns from
        one scope never get attributed to tables that only appear in a
        different scope.
      - A QUALIFIED column (t.col) resolves alias `t` to its real table
        name using THIS scope's own FROM/JOIN sources. If the qualifier
        doesn't resolve to a real base table in this scope (e.g. it's a
        subquery/CTE alias instead), it's mapped to DEFAULT_TABLE rather
        than guessed at.
      - An UNQUALIFIED column that matches one of this scope's own
        SELECT-list output aliases (e.g. WHERE DRUG_TYPE_IND <> '?' where
        DRUG_TYPE_IND is a `CASE...END AS DRUG_TYPE_IND` in the SELECT)
        is a self-reference to a computed value, not a real table column
        — mapped to DEFAULT_TABLE.
      - An UNQUALIFIED column is attributed to the single table in scope
        when unambiguous; when the scope joins more than one real table,
        it's genuinely ambiguous and mapped to DEFAULT_TABLE rather than
        force-attributed to every table.
    """
    if isinstance(node, (exp.Update, exp.Merge)):
        return _extract_from_unscopable_dml(node)

    try:
        root = build_scope(node)
    except Exception:
        root = None

    if root is None:
        # Fallback for fragments build_scope can't handle — keep the old
        # best-effort behaviour rather than dropping the row entirely.
        tables = [t.name.upper() for t in node.find_all(exp.Table) if t.name]
        cols   = list(dict.fromkeys(c.name.upper() for c in node.find_all(exp.Column) if c.name))
        stars  = list(node.find_all(exp.Star))
        pairs  = []
        if stars and not cols:
            for tbl in tables:
                pairs.append((tbl, "*"))
        else:
            for tbl in tables:
                for col in cols:
                    pairs.append((tbl, col))
        return pairs

    pairs = []
    for scope in root.traverse():
        alias_to_table = {
            alias.upper(): source.name.upper()
            for alias, source in scope.sources.items()
            if isinstance(source, exp.Table) and source.name
        }
        if not alias_to_table:
            continue

        scope_tables = list(dict.fromkeys(alias_to_table.values()))

        output_alias_names = {
            sel.alias.upper() for sel in getattr(scope.expression, "selects", [])
            if isinstance(sel, exp.Alias)
        }

        has_star = any(
            isinstance(sel, exp.Star) or
            (isinstance(sel, exp.Column) and isinstance(sel.this, exp.Star))
            for sel in getattr(scope.expression, "selects", [])
        )

        scope_columns = [c for c in scope.columns if c.name]

        if has_star and not scope_columns:
            for tbl in scope_tables:
                pairs.append((tbl, "*"))
            continue

        seen = set()
        for col in scope_columns:
            col_name = col.name.upper()
            tbl_ref  = col.table.upper() if col.table else None

            if tbl_ref:
                real_tbl = alias_to_table.get(tbl_ref)
                key = (real_tbl, col_name) if real_tbl else (DEFAULT_TABLE, col_name)
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)
                continue

            if col_name in output_alias_names:
                key = (DEFAULT_TABLE, col_name)
            elif len(scope_tables) == 1:
                key = (scope_tables[0], col_name)
            else:
                key = (DEFAULT_TABLE, col_name)

            if key not in seen:
                seen.add(key)
                pairs.append(key)

    return pairs


def extract_table_column_pairs(raw_sql: str) -> tuple:
    """
    Returns (pairs, reason):
      pairs  — list of (TABLE, COLUMN) tuples, uppercased
      reason — None on success, string description if the SQL was unparseable

    Cases handled:
      CASE 1 — Genuine Teradata utility commands (SHOW/HELP/COLLECT/EXEC):
               sqlglot returns a Command node with no AST children.
               Logged & skipped, same as before.
      CASE 1b — CREATE ... AS (...) wrapping a LOCKING ROW FOR ACCESS (or
               similar) clause: sqlglot also returns Command for the WHOLE
               statement here, even though the inner query is perfectly
               valid Teradata SQL on its own. We recover by extracting the
               inner SELECT/WITH body, stripping the locking clause, and
               re-parsing just that — using ONLY the Teradata dialect (no
               cross-dialect fallback, to avoid other dialects silently
               "recovering" genuinely malformed SQL as something benign).
      CASE 2 — SELECT *: sqlglot returns Star nodes, not Column nodes →
               recorded as (TABLE, '*').
      CASE 3 — Normal SELECT/INSERT/UPDATE/MERGE/DELETE, and CREATE ... AS
               (SELECT ...) that parses fine directly → scope-based (or,
               for UPDATE/MERGE, manual-alias) extraction — see
               _extract_from_node — restricted to the query subtree
               (search_root) so the object being CREATEd isn't itself
               counted as a "referenced" table.
    """
    sql    = raw_sql.replace('""', '"').strip()
    sql    = re.sub(r'--[^\n]*', '', sql)       # strip  -- comments
    pairs  = []
    reason = None

    try:
        statements = sqlglot.parse(
            sql,
            dialect     = "teradata",
            error_level = sqlglot.ErrorLevel.IGNORE
        )
        for stmt in statements:
            if stmt is None:
                continue

            # CASE 1 / 1b: Teradata Command fallback
            if type(stmt).__name__ == "Command":
                inner_sql = _extract_inner_query(sql)
                recovered_pairs = []
                if inner_sql:
                    try:
                        inner_statements = sqlglot.parse(
                            inner_sql,
                            dialect     = "teradata",
                            error_level = sqlglot.ErrorLevel.IGNORE
                        )
                    except Exception:
                        inner_statements = []
                    if inner_statements and not any(
                        type(s).__name__ == "Command" for s in inner_statements if s is not None
                    ):
                        for inner_stmt in inner_statements:
                            if inner_stmt is not None:
                                recovered_pairs.extend(_extract_from_node(inner_stmt))

                if recovered_pairs:
                    pairs.extend(recovered_pairs)   # recovered — not a real skip
                else:
                    reason = f"Teradata utility command: {sql.strip()[:80]}"
                continue

            # search_root: for CREATE ... AS (SELECT ...), only look inside
            # the query part so the view/table name being CREATEd isn't
            # itself counted as a "referenced" table.
            search_root = stmt
            if isinstance(stmt, exp.Create):
                inner_expr = stmt.args.get("expression")
                if inner_expr is not None:
                    search_root = inner_expr

            pairs.extend(_extract_from_node(search_root))

    except Exception as exc:
        reason = f"Parse error: {exc}"

    if pairs:
        reason = None

    return pairs, reason
