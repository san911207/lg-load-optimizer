"""
=========================================================================
Packing Engine - py3dbp 기반 3D Bin Packing
=========================================================================
- 가전 특화 제약조건 처리: this_side_up, stackable, load_bear_kg, fragile
- 트럭 좌표계: x=length(전후), y=width(좌우), z=height(상하)
- py3dbp 내부는 (W,H,D) 순서이므로 매핑:
    bin.width  = truck.length  (x축, 전후)
    bin.height = truck.height  (z축, 상하)
    bin.depth  = truck.width   (y축, 좌우)
=========================================================================
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from py3dbp import Packer, Bin, Item


# py3dbp의 회전 타입 (0~5)
# 0: W,H,D (원본)
# 1: H,W,D
# 2: H,D,W
# 3: D,H,W
# 4: D,W,H
# 5: W,D,H

# this_side_up=True 이면 상하면 회전 금지 → 0,1 만 허용
# (z축 = height 그대로 유지되는 회전만)
ROTATIONS_THIS_SIDE_UP = [0, 1]      # H 그대로
ROTATIONS_FREE         = [0, 1, 2, 3, 4, 5]


@dataclass
class PackedItem:
    seq: int
    model_code: str
    category: str
    pos_x: float     # mm, 트럭 length 방향
    pos_y: float     # mm, 트럭 width 방향
    pos_z: float     # mm, 트럭 height 방향
    dim_x: float     # 배치된 후 x방향 크기
    dim_y: float
    dim_z: float
    weight_kg: float
    rotation: int
    zone: str = ""   # 'A1','B2' 등 그리드 라벨


@dataclass
class PackResult:
    load_id: str
    truck_type: str
    truck_display: str
    truck_length_mm: int
    truck_width_mm: int
    truck_height_mm: int
    truck_volume_cbm: float
    truck_max_payload_kg: float

    fitted_items: List[PackedItem] = field(default_factory=list)
    unfitted_items: List[Dict]     = field(default_factory=list)

    used_volume_cbm: float = 0.0
    used_weight_kg: float  = 0.0

    @property
    def volume_util_pct(self) -> float:
        if self.truck_volume_cbm == 0: return 0.0
        return round(self.used_volume_cbm / self.truck_volume_cbm * 100, 2)

    @property
    def weight_util_pct(self) -> float:
        if self.truck_max_payload_kg == 0: return 0.0
        return round(self.used_weight_kg / self.truck_max_payload_kg * 100, 2)

    @property
    def fitted_count(self) -> int:
        return len(self.fitted_items)

    @property
    def unfitted_count(self) -> int:
        return sum(u["quantity"] for u in self.unfitted_items)


def _assign_zone(pos_x: float, truck_length: float, n_zones: int = 4) -> str:
    """트럭 길이를 n등분해서 A,B,C,D 존 라벨 부여 (작업자 가이드용)"""
    zone_size = truck_length / n_zones
    idx = min(int(pos_x // zone_size), n_zones - 1)
    return "ABCD"[idx]


def simulate_pack(
    load_id: str,
    order_lines: List[Dict],   # [{model_code, quantity, ...}]
    model_master: Dict[str, Dict],  # {model_code: {...spec...}}
    truck_spec: Dict,          # {truck_type, length_mm, width_mm, height_mm, max_payload_kg, ...}
    bigger_first: bool = True,
) -> PackResult:
    """단일 Load_ID에 대해 적재 시뮬레이션 실행"""

    packer = Packer()

    bin_obj = Bin(
        name=truck_spec["truck_type"],
        width  = truck_spec["length_mm"],    # x축 (트럭 length 방향)
        height = truck_spec["height_mm"],    # z축 (상하)
        depth  = truck_spec["width_mm"],     # y축 (좌우)
        max_weight = truck_spec["max_payload_kg"],
    )
    packer.add_bin(bin_obj)

    # 주문 라인을 개별 박스로 풀어서 추가
    item_meta: Dict[str, Dict] = {}  # item.name → spec
    seq = 0
    for line in order_lines:
        mc = line["model_code"]
        qty = int(line["quantity"])
        if mc not in model_master:
            raise ValueError(f"Model not in master: {mc}")
        spec = model_master[mc]

        for i in range(qty):
            seq += 1
            item_name = f"{mc}#{seq:04d}"
            # py3dbp: (width, height, depth) = (제품 W, 제품 H, 제품 D)
            # 우리는 제품 W → x, 제품 D → y, 제품 H → z 매핑
            item = Item(
                name=item_name,
                width  = spec["width_mm"],    # x 방향
                height = spec["height_mm"],   # z 방향
                depth  = spec["depth_mm"],    # y 방향
                weight = spec["weight_kg"],
            )
            # 회전 제약: this_side_up=True면 상하 회전 금지
            if spec.get("this_side_up", True):
                item.allowed_rotation = ROTATIONS_THIS_SIDE_UP
            else:
                item.allowed_rotation = ROTATIONS_FREE

            packer.add_item(item)
            item_meta[item_name] = {
                "model_code": mc,
                "category": spec.get("category", ""),
                "fragile": spec.get("fragile", False),
                "stackable": spec.get("stackable", False),
            }

    # 패킹 실행
    packer.pack(
        bigger_first=bigger_first,
        distribute_items=False,
        number_of_decimals=0,
    )

    # 결과 정리
    result = PackResult(
        load_id=load_id,
        truck_type=truck_spec["truck_type"],
        truck_display=truck_spec.get("display_name", truck_spec["truck_type"]),
        truck_length_mm=truck_spec["length_mm"],
        truck_width_mm=truck_spec["width_mm"],
        truck_height_mm=truck_spec["height_mm"],
        truck_volume_cbm=(truck_spec["length_mm"]*truck_spec["width_mm"]*truck_spec["height_mm"]) / 1_000_000_000,
        truck_max_payload_kg=truck_spec["max_payload_kg"],
    )

    fitted_seq = 0
    used_vol_mm3 = 0
    for b in packer.bins:
        for it in b.items:
            fitted_seq += 1
            # py3dbp의 결과: position=[x,y,z], width/height/depth는 회전 후 효과적 크기를
            # rotation_type에 따라 계산해야 함. 안전하게 get_dimension() 사용
            try:
                dims = it.get_dimension()  # [W,H,D] (회전 적용된 effective)
                ex, ez, ey = float(dims[0]), float(dims[1]), float(dims[2])
            except Exception:
                ex, ez, ey = float(it.width), float(it.height), float(it.depth)

            px, pz, py = (float(it.position[0]), float(it.position[1]), float(it.position[2]))
            # py3dbp position 매핑: position[0]=x(width축), [1]=z(height축), [2]=y(depth축)

            meta = item_meta.get(it.name, {})
            pi = PackedItem(
                seq=fitted_seq,
                model_code=meta.get("model_code","?"),
                category=meta.get("category",""),
                pos_x=px, pos_y=py, pos_z=pz,
                dim_x=ex, dim_y=ey, dim_z=ez,
                weight_kg=float(it.weight),
                rotation=int(it.rotation_type),
                zone=_assign_zone(px, truck_spec["length_mm"]),
            )
            result.fitted_items.append(pi)
            used_vol_mm3 += ex*ey*ez
            result.used_weight_kg += float(it.weight)

        # 미적재 품목 집계
        unfit_count: Dict[str, int] = {}
        for it in b.unfitted_items:
            meta = item_meta.get(it.name, {})
            mc = meta.get("model_code","?")
            unfit_count[mc] = unfit_count.get(mc, 0) + 1
        for mc, q in unfit_count.items():
            result.unfitted_items.append({"model_code": mc, "quantity": q})

    result.used_volume_cbm = round(used_vol_mm3 / 1_000_000_000, 4)
    result.used_weight_kg = round(result.used_weight_kg, 2)
    return result
