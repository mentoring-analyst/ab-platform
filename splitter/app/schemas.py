from pydantic import BaseModel, Field, field_validator


class VariantDef(BaseModel):
    name: str = Field(min_length=1, max_length=16)
    share: int = Field(ge=1, le=99, description="Доля от ВСЕГО трафика аудитории, в процентах")


def validate_variants(v: list["VariantDef"]) -> list["VariantDef"]:
    if len(v) < 2:
        raise ValueError("нужно минимум 2 варианта")
    names = [x.name for x in v]
    if len(set(names)) != len(names):
        raise ValueError("имена вариантов должны быть уникальны")
    total = sum(x.share for x in v)
    if total > 100:
        raise ValueError(f"сумма долей не может превышать 100% трафика, сейчас {total}")
    return v


class AudienceFilters(BaseModel):
    regions: list[str] | None = None
    platforms: list[str] | None = None
    min_trips_preperiod: int = 0


class ExperimentCreate(BaseModel):
    code: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str
    hypothesis: str = ""
    owner: str = ""
    variants: list[VariantDef]
    audience_filters: AudienceFilters = AudienceFilters()

    @field_validator("variants")
    @classmethod
    def shares_valid(cls, v):
        return validate_variants(v)


class ExperimentUpdate(BaseModel):
    """Частичное обновление. Название/гипотезу/владельца можно менять всегда,
    варианты и аудиторию — только у черновика (это проверяет обработчик)."""
    name: str | None = None
    hypothesis: str | None = None
    owner: str | None = None
    variants: list[VariantDef] | None = None
    audience_filters: AudienceFilters | None = None

    @field_validator("variants")
    @classmethod
    def shares_valid(cls, v):
        return validate_variants(v) if v is not None else v


class MetricCreate(BaseModel):
    code: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str
    kind: str = Field(pattern=r"^(proportion|mean|ratio)$")
    sql_template: str
    description: str = ""


class AttachMetric(BaseModel):
    metric_code: str
    role: str = Field(pattern=r"^(target|proxy|guardrail)$")


class BatchAssignRequest(BaseModel):
    user_ids: list[int] = Field(max_length=50000)
    # Виртуальное время exposure; если не передано — берётся из sim_clock
    virtual_ts: str | None = None
