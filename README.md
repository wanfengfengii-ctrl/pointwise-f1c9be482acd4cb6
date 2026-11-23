# 青岭泵站双区测振归档

白班采集员把测振设备导出的有序原页交给资料室。维护员根据仪表校准结果重算整卷。夜班在采集终端下线后分次领页，核账员把有出处的整数微米明细和合计交给下一班维护负责人。领用跨重标生效时，票据继续兑现开票时那一整卷。

## 一天的工作

1. 采集员交入 `samples/pump_shift.json` 中的两页。A、B 的原测值分别为 7、11 μm，两个原页分别保留自己的测次与出处。
2. 保管员初封。同材重投取回原初封回执，变更原值、页序、测次或出处的重投触发冲突。
3. 夜班开票并取走第一页 7 μm，终端保存这次成功领页和下一页位置。
4. 维护员用 2 倍重标。候选写到第一页时，夜班仍可按初版开票并领到 7、11。若第二页写失败，重开后当前版仍可领取 7、11。
5. 两页均已写成并核对后，2 倍版生效。旧票在终端重开后续得 11，新票领到 14、22。核账员得到初版 18 μm 和 2 倍版 36 μm 的班账。
6. 当前版和未关旧票分别保全两个区时，下一次重标返回占用。第一张旧票关闭仍不足以释放旧区。最后一张旧票关闭后，3 倍重标生成 21、33，下一班领用并核出 54 μm。
7. 维护负责人按来源卷、原页、页内位置和随附出处核对班账。核账员关票以结束该票的续领、重算和载荷保全资格。

## 运行

使用 Python 3.11 标准库，在项目根目录执行。`check_project.py` 是控制台物化项目时提供的完整回归入口。

```text
python3 -B check_project.py
python3 -B -m demo
```

演示使用临时归档，会实际初封、领页、注入一次写失败、重开、重标和核账。它在断言通过后输出 `POINTWISE_SCENARIO` 的实际页值、出处和班账。回归还覆盖非法输入、候选缺页与失败重试、票据读失败和重开、证据有缺口、有效零与全缺测。

## Python API

```python
from pathlib import Path

from archive.catalog import Catalog
from calibration.job import recalibrate_volume
from delivery.close import close_ticket
from delivery.read_page import DeliveryComplete, next_page
from delivery.ticket import open_ticket
from intake.initial_seal import seal_source
from reconcile.statement import reconcile_ticket

catalog = Catalog(Path('station')).initialize()
initial = seal_source(catalog, {
    'source_id': 'day-17',
    'pages': [{'page_id': 'sheet-A', 'readings': [
        {'reading_id': 'A', 'raw_um': 7, 'origin': 'pump-A/inboard'},
    ]}],
})
revision = recalibrate_volume(catalog, initial.volume_id, 2)
ticket = open_ticket(catalog, initial.volume_id)
page = next_page(catalog, ticket.ticket_id)
assert page.revision == revision.revision
assert page.page.readings[0].value_um == 14
assert isinstance(next_page(catalog, ticket.ticket_id), DeliveryComplete)
statement = reconcile_ticket(catalog, ticket.ticket_id)
assert statement.totals.total_um == 14
close_ticket(catalog, ticket.ticket_id)
```

- `Catalog(root).initialize()` 可重复调用。使用同一根目录可恢复已发布版、初封回执和票据。载荷区属于整个终端，所有卷共用两个位置。多个当前卷也会各自占用位置。
- `seal_source` 接受上述映射或 `SourceVolume`，其页和测次也可分别采用 `SourcePage` 和 `SourceReading`。卷号、页号、测次号及出处是非空文本。页号及测次号在卷内各自唯一。卷和页至少有一项。原值接受整数或 `None`。格式错误抛出 `SourceValidationError`，同来源卷的异材重投抛出 `SourceConflictError`。回执 `SourceManifest` 包含 `volume_id`、`initial_revision` 和有序原清册。
- `recalibrate_volume` 接受已封卷号和正整数倍率，返回含 `revision` 的 `RevisionLocation`。倍率错误抛出 `InvalidMultiplierError`，未知卷抛出 `UnknownVolumeError`，没有可写区抛出 `RegionsOccupiedError`，另一个写卷操作进行中抛出 `ArchiveBusyError`。写入或候选校验出错会抛出相应异常，当前版在整卷生效前保持。
- `open_ticket` 返回含 `ticket_id`、`volume_id`、`revision` 的 `Ticket`。未知卷抛出 `UnknownVolumeError`。`next_page` 返回 `DeliveredPage`，其中 `page` 提供从零开始的页序、原页号和按输入次序排列的测次。页内位置为测次在元组中的下标。卷尾反复调用均返回 `DeliveryComplete`。未知票抛出 `UnknownTicketError`，已关票抛出 `ClosedTicketError`，读失败抛出 `OSError` 且保留页位。
- `reconcile_ticket` 对未关票返回 `ShiftStatement` 或 `PendingStatement`。明细按页与页内原序排列，带测次、原值、换算值和出处。合计精确累加非空整数微米，`valid_count` 包括零，`missing_count` 仅计 `None`。全空合计为 0。完整未关票可以重复核账。
- `close_ticket` 返回 `CloseResult.CLOSED` 或 `CloseResult.ALREADY_CLOSED`，未知票抛出 `UnknownTicketError`。一个非当前版最后一张票关闭后，对应载荷区可被下一次写卷操作保留。

## 持久化与恢复

归档根目录由程序管理。`catalog.sqlite3` 只存目录元数据与摘要，`payload/a` 和 `payload/b` 各有一个整卷位置。页内微米数以有符号十六进制文本无损编码，缺测为 JSON `null`。API 返回 Python 整数或 `None`。目录作业锁序列化写卷操作，逐页写入期间允许开票和领取。写入完成后登记候选页，用候选自己的成功写页清册和实际可读页做发布核验。失败候选的文件可能留在已释放的区，下次保留使用新的版次和写页清册。正在执行的写卷操作异常退出后，下一次写卷会在取得作业锁后清理其未提交的候选。

`Catalog` 支持三个可选 I/O 回调 `before_write`、`after_write` 和 `before_read`，参数均为物理区名及从零开始的页序。`after_write` 在候选页写入及登记后、目录事务之外调用，可用另一个目录句柄实际开票或读页。测试在指定页边界用回调暂停或抛出 I/O 错误。
