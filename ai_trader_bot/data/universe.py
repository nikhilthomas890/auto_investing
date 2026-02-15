from __future__ import annotations

COMPUTE_THEME_QUERIES: dict[str, str] = {
    "NVDA": "NVIDIA AI chips data center GPU",
    "AMD": "AMD MI300 AI accelerator data center",
    "AVGO": "Broadcom AI networking custom silicon",
    "TSM": "TSMC AI semiconductor foundry",
    "ASML": "ASML EUV lithography AI chip demand",
    "MU": "Micron HBM memory AI demand",
    "ARM": "Arm AI CPU architecture data center",
    "MRVL": "Marvell AI networking silicon",
    "AMAT": "Applied Materials semiconductor equipment AI demand",
    "LRCX": "Lam Research wafer fabrication AI chips",
}

INFRA_THEME_QUERIES: dict[str, str] = {
    "MSFT": "Microsoft Azure OpenAI AI cloud infrastructure",
    "AMZN": "Amazon AWS Bedrock Trainium Inferentia AI cloud",
    "GOOGL": "Google Gemini TPU cloud AI infrastructure",
    "META": "Meta Llama AI infrastructure data center",
    "ANET": "Arista AI ethernet interconnect data center",
    "SMCI": "Supermicro AI server infrastructure",
    "DELL": "Dell AI server data center infrastructure",
    "VRT": "Vertiv data center cooling power AI buildout",
    "EQIX": "Equinix data center colocation AI infrastructure",
    "DLR": "Digital Realty hyperscaler data center AI demand",
    "ETN": "Eaton electrical power systems AI data centers",
    "CEG": "Constellation Energy power demand AI data centers",
}

SOFTWARE_THEME_QUERIES: dict[str, str] = {
    "ORCL": "Oracle AI cloud database infrastructure",
    "SNOW": "Snowflake AI data platform enterprise",
    "PLTR": "Palantir AI platform government enterprise",
    "CRM": "Salesforce Einstein AI software platform",
    "NOW": "ServiceNow enterprise workflow generative AI",
    "MDB": "MongoDB AI application data platform",
    "DDOG": "Datadog observability AI cloud workloads",
    "NET": "Cloudflare AI inference edge platform",
    "ADBE": "Adobe Firefly AI software platform",
}

MATERIALS_THEME_QUERIES: dict[str, str] = {
    "FCX": "Freeport McMoRan copper supply data center AI",
    "SCCO": "Southern Copper copper demand data centers AI",
    "MP": "MP Materials rare earth magnets AI supply chain",
    "ALB": "Albemarle lithium energy storage data center",
    "SQM": "SQM lithium supply chain data center power",
}

SPACE_THEME_QUERIES: dict[str, str] = {
    "RKLB": "Rocket Lab launch services satellite infrastructure AI",
    "ASTS": "AST SpaceMobile satellite broadband connectivity AI edge",
    "IRDM": "Iridium satellite communications resilient network infrastructure",
    "SPIR": "Spire Global satellite data analytics AI geospatial",
    "PL": "Planet Labs earth observation satellite imagery AI",
    "LMT": "Lockheed Martin space systems missile defense satellite AI",
    "NOC": "Northrop Grumman space systems satellite infrastructure",
    "RTX": "RTX aerospace defense sensors radar space systems AI",
}

AI_THEME_QUERIES: dict[str, str] = {
    **COMPUTE_THEME_QUERIES,
    **INFRA_THEME_QUERIES,
    **SOFTWARE_THEME_QUERIES,
    **MATERIALS_THEME_QUERIES,
    **SPACE_THEME_QUERIES,
}

QUANTUM_QUERIES: dict[str, str] = {
    "IONQ": "IonQ quantum computing",
    "RGTI": "Rigetti quantum computing",
    "QBTS": "D-Wave quantum computing",
}


def build_theme_map(symbols: list[str], include_quantum: bool) -> dict[str, str]:
    theme_map = dict(AI_THEME_QUERIES)
    if include_quantum:
        theme_map.update(QUANTUM_QUERIES)

    resolved: dict[str, str] = {}
    for symbol in symbols:
        clean = symbol.strip().upper()
        if not clean:
            continue
        resolved[clean] = theme_map.get(
            clean,
            f"{clean} AI compute infrastructure software platform data center raw materials space",
        )

    return resolved
