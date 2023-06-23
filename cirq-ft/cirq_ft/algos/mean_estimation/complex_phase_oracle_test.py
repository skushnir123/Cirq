# Copyright 2023 The Cirq Developers
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import math
from typing import Optional

import bitstring
from fixedpoint import FixedPoint

import cirq
import cirq_ft
import numpy as np
import pytest
from attr import frozen
from cirq._compat import cached_property
from cirq_ft.algos import random_variable_encoder
from cirq_ft.algos.mean_estimation.complex_phase_oracle import ComplexPhaseOracle
from cirq_ft.infra import bit_tools
from cirq_ft.infra import testing as cq_testing


@frozen
class DummySelect(random_variable_encoder.RandomVariableEncoder):
    target_bitsize_before_decimal: int
    target_bitsize_after_decimal: int
    control_val: Optional[int] = None

    @cached_property
    def bitsize(self):
        return self.target_bitsize_before_decimal + self.target_bitsize_after_decimal

    @cached_property
    def control_registers(self) -> cirq_ft.Registers:
        registers = [] if self.control_val is None else [cirq_ft.Register('control', 1)]
        return cirq_ft.Registers(registers)

    @cached_property
    def selection_registers(self) -> cirq_ft.SelectionRegisters:
        return cirq_ft.SelectionRegisters.build(selection=(self.bitsize, 2**self.bitsize))

    @cached_property
    def target_registers(self) -> cirq_ft.Registers:
        return cirq_ft.Registers.build(target=self.bitsize)

    def decompose_from_registers(self, context, selection, target):
        yield [cirq.CNOT(s, t) for s, t in zip(selection, target)]


@pytest.mark.parametrize('bitsize_before_decimal', [3, 2, 3, 2])
@pytest.mark.parametrize('bitsize_after_decimal', [1, 2, 0, 1])
@pytest.mark.parametrize('arctan_bitsize', [8])
def test_phase_oracle(bitsize_before_decimal: int, bitsize_after_decimal, arctan_bitsize: int):
    bitsize = bitsize_before_decimal + bitsize_after_decimal
    phase_oracle = ComplexPhaseOracle(
        DummySelect(bitsize_before_decimal, bitsize_after_decimal), arctan_bitsize
    )
    g = cq_testing.GateHelper(phase_oracle)

    # Prepare uniform superposition state on selection register and apply phase oracle.
    circuit = cirq.Circuit(cirq.H.on_each(*g.quregs['selection']))
    circuit += cirq.Circuit(cirq.decompose_once(g.operation))

    # Simulate the circut and test output.
    qubit_order = cirq.QubitOrder.explicit(g.quregs['selection'], fallback=cirq.QubitOrder.DEFAULT)
    result = cirq.Simulator(dtype=np.complex128).simulate(circuit, qubit_order=qubit_order)
    state_vector = result.final_state_vector
    state_vector = state_vector.reshape(2**bitsize, len(state_vector) // 2**bitsize)
    prepared_state = state_vector.sum(axis=1)
    for x in range(2**bitsize):
        x_float = float(
            FixedPoint(
                f"0b_{bitstring.BitArray(uint=x, length=bitsize).bin}",
                signed=False,
                m=bitsize_before_decimal,
                n=bitsize_after_decimal,
                str_base=2,
            )
        )
        output_val = -2 * np.arctan(x_float, dtype=np.double) / np.pi
        output_bits = [*bit_tools.iter_bits_fixed_point(np.abs(output_val), arctan_bitsize)]
        approx_val = np.sign(output_val) * math.fsum(
            [b * (1 / 2 ** (1 + i)) for i, b in enumerate(output_bits)]
        )

        assert math.isclose(output_val, approx_val, abs_tol=1 / 2**bitsize), output_bits

        y = np.exp(1j * approx_val * np.pi) / np.sqrt(2**bitsize)
        assert np.isclose(prepared_state[x], y)


def test_phase_oracle_consistent_protocols():
    bitsize_before_decimal, bitsize_after_decimal, arctan_bitsize = 3, 0, 5
    gate = ComplexPhaseOracle(
        DummySelect(bitsize_before_decimal, bitsize_after_decimal, 1), arctan_bitsize
    )
    expected_symbols = ('@',) + ('ROTy',) * (bitsize_before_decimal + bitsize_after_decimal)
    assert cirq.circuit_diagram_info(gate).wire_symbols == expected_symbols
