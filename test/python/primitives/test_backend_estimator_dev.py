# This code is part of Qiskit.
#
# (C) Copyright IBM 2022.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""Unit tests for Estimator."""

from unittest import TestCase
from unittest.mock import Mock, patch

from ddt import ddt, data, unpack
import numpy as np

from qiskit import QuantumCircuit
from qiskit.primitives.backend_estimator_dev import (
    AbelianDecomposer,
    BackendEstimator,
    NaiveDecomposer,
)
from qiskit.providers import Backend
from qiskit.quantum_info.operators import Pauli, SparsePauliOp
from qiskit.quantum_info.operators.symplectic.pauli_list import PauliList
from qiskit.transpiler import Layout, PassManager
from qiskit.transpiler.passes import ApplyLayout, SetLayout


################################################################################
## AUXILIARY
################################################################################
def measurement_circuit_examples():
    """Generator of commuting Paulis and corresponding measurement circuits."""
    I = QuantumCircuit(1, 1)  # pylint: disable=invalid-name
    I.measure(0, 0)
    yield ["I", "Z"], I

    X = QuantumCircuit(1, 1)  # pylint: disable=invalid-name
    X.h(0)
    X.measure(0, 0)
    yield ["X", "I"], X

    Y = QuantumCircuit(1, 1)  # pylint: disable=invalid-name
    Y.sdg(0)
    Y.h(0)
    Y.measure(0, 0)
    yield ["Y", "I"], Y

    Z = QuantumCircuit(1, 1)  # pylint: disable=invalid-name
    Z.measure(0, 0)
    yield ["Z", "I"], Z

    II = QuantumCircuit(2, 1)  # pylint: disable=invalid-name
    II.measure(0, 0)
    yield ["II"], II

    IY = QuantumCircuit(2, 1)  # pylint: disable=invalid-name
    IY.sdg(0)
    IY.h(0)
    IY.measure(0, 0)
    yield ["IY", "II"], IY

    XY = QuantumCircuit(2, 2)  # pylint: disable=invalid-name
    XY.h(1)
    XY.sdg(0)
    XY.h(0)
    XY.measure([0, 1], [0, 1])
    yield ["XY", "II", "XI", "IY"], XY

    XX = QuantumCircuit(2, 2)  # pylint: disable=invalid-name
    XX.h(1)
    XX.h(0)
    XX.measure([0, 1], [0, 1])
    yield ["XX", "IX", "XI", "II"], XX

    ZZ = QuantumCircuit(2, 2)  # pylint: disable=invalid-name
    ZZ.measure([0, 1], [0, 1])
    yield ["ZZ", "IZ", "ZI", "II"], ZZ

    XYZ = QuantumCircuit(3, 3)  # pylint: disable=invalid-name
    XYZ.h(2)
    XYZ.sdg(1)
    XYZ.h(1)
    XYZ.measure([0, 1, 2], [0, 1, 2])
    yield ["XYZ", "XII", "IYI", "IIZ", "XIZ", "III"], XYZ

    YIX = QuantumCircuit(3, 2)  # pylint: disable=invalid-name
    YIX.sdg(2)
    YIX.h(2)
    YIX.h(0)
    YIX.measure([0, 2], [0, 1])
    yield ["YIX", "IIX", "YII", "III"], YIX

    IXII = QuantumCircuit(4, 1)  # pylint: disable=invalid-name
    IXII.h(2)
    IXII.measure(2, 0)
    yield ["IXII", "IIII"], IXII


################################################################################
## TESTS
################################################################################
@ddt
class TestTranspilation(TestCase):
    """Test transpilation logic."""

    @data(
        [3, (0, 1, 2)],
        [4, (0, 1, 2, 3)],
        [4, (1, 3, 2, 0)],
        [4, (0, 1, 3)],
        [4, (3, 1)],
    )
    @unpack
    def test_transpile(self, target_qubits, layout_intlist):
        """Test transpile functionality."""
        # Input and measured circuits
        num_qubits = len(layout_intlist)
        input_circuit = QuantumCircuit(num_qubits)
        measured_circuit = input_circuit.copy()
        measured_circuit.measure_all()
        # Transpiled circuit (only changes layout and num of qubits)
        layout_dict = dict.fromkeys(range(target_qubits))
        layout_dict.update(dict(zip(layout_intlist, input_circuit.qubits)))
        applied_layout = Layout(layout_dict)
        passes = [SetLayout(layout=applied_layout), ApplyLayout()]
        pass_manager = PassManager(passes=passes)
        transpiled_circuit = pass_manager.run(measured_circuit)  # TODO: skip_transpilation
        # Test patching terra's transpile call
        backend = Mock(Backend)
        estimator = BackendEstimator(backend)
        with patch("qiskit.primitives.backend_estimator_dev.transpile", spec=True) as mock:
            mock.return_value = transpiled_circuit
            output_circuit = estimator._transpile(input_circuit)
        mock.assert_called_once()
        call_circuit, call_backend = mock.call_args.args  # TODO: transpile options
        self.assertEqual(call_circuit, measured_circuit)
        self.assertIs(call_backend, backend)
        self.assertEqual(output_circuit, transpiled_circuit)
        self.assertIsInstance(output_circuit, QuantumCircuit)
        inferred_layout = output_circuit.metadata.get("final_layout")
        self.assertEqual(inferred_layout, applied_layout)
        self.assertIsInstance(inferred_layout, Layout)

    @data(
        [3, (0, 1, 2)],
        [4, (0, 1, 2, 3)],
        [4, (1, 3, 2, 0)],
        [4, (0, 1, 3)],
        [4, (3, 1)],
    )
    @unpack
    def test_infer_final_layout(self, target_qubits, layout_intlist):
        """Test final layouts inferred from measurements.

        Assumptions:
            - Original measurements are in order (i.e. coming from `measure_all()`)
            - Classical bits remain in order in measurements after transpilation
        """
        # Original circuit
        num_qubits = len(layout_intlist)
        original_circuit = QuantumCircuit(num_qubits)
        original_circuit.measure_all()
        # Transpiled circuit (only changes layout and num of qubits)
        layout_dict = dict.fromkeys(range(target_qubits))
        layout_dict.update(dict(zip(layout_intlist, original_circuit.qubits)))
        applied_layout = Layout(layout_dict)
        passes = [SetLayout(layout=applied_layout), ApplyLayout()]
        pass_manager = PassManager(passes=passes)
        transpiled_circuit = pass_manager.run(original_circuit)
        # Test
        inferred_layout = BackendEstimator._infer_final_layout(original_circuit, transpiled_circuit)
        self.assertEqual(inferred_layout, applied_layout)
        self.assertIsInstance(inferred_layout, Layout)

    def test_run_bound_pass_manager(self):
        """Test bound pass manager runs."""
        backend = Mock(Backend)
        estimator = BackendEstimator(backend)
        # Invalid input
        self.assertRaises(TypeError, estimator._run_bound_pass_manager, "circuit")
        # No pass manager
        circuit = Mock(QuantumCircuit)
        self.assertIs(circuit, estimator._run_bound_pass_manager(circuit))
        # Pass manager runs
        mock_circuit = Mock(QuantumCircuit)
        estimator._bound_pass_manager = Mock(PassManager)
        estimator._bound_pass_manager.run.return_value = mock_circuit
        self.assertIs(mock_circuit, estimator._run_bound_pass_manager(circuit))
        estimator._bound_pass_manager.run.assert_called_once_with(circuit)


@ddt
class TestMeasurement(TestCase):
    """Test measurement logic."""

    def test_observable_decomposer(self):
        """Test observable decomposer property."""
        estimator = BackendEstimator(Mock(Backend))
        self.assertIs(estimator.abelian_grouping, True)
        self.assertIsInstance(estimator._observable_decomposer, AbelianDecomposer)
        self.assertIsNot(estimator._observable_decomposer, estimator._observable_decomposer)
        estimator.abelian_grouping = False
        self.assertIsInstance(estimator._observable_decomposer, NaiveDecomposer)
        self.assertIsNot(estimator._observable_decomposer, estimator._observable_decomposer)

    @data(*measurement_circuit_examples())
    @unpack
    def test_build_single_measurement_circuit(self, paulis, measurement):
        """Test measurement circuits for a given observable."""
        observable = SparsePauliOp(paulis)
        circuit = BackendEstimator(Mock(Backend))._build_single_measurement_circuit(observable)
        self.assertEqual(circuit, measurement)
        # TODO: circuit.metadata
        meas_indices = circuit.metadata.get("measured_qubit_indices")
        paulis = PauliList.from_symplectic(
            observable.paulis.z[:, meas_indices],
            observable.paulis.x[:, meas_indices],
            observable.paulis.phase,
        )
        self.assertIsInstance(circuit.metadata.get("paulis"), PauliList)
        self.assertEqual(circuit.metadata.get("paulis"), paulis)
        self.assertIsInstance(circuit.metadata.get("coeffs"), tuple)
        self.assertTrue(
            all(
                md == c
                for md, c in zip(
                    circuit.metadata.get("coeffs"), np.real_if_close(observable.coeffs)
                )
            )
        )

    @data(*measurement_circuit_examples())
    @unpack
    def test_build_pauli_measurement(self, paulis, measurement):
        """Test Pauli measurement circuit from Pauli."""
        # TODO: test too similar to implementation
        pauli = Pauli(paulis[0])
        meas_indices = tuple(i for i, p in enumerate(pauli) if p != Pauli("I")) or (0,)
        circuit = BackendEstimator(Mock(Backend))._build_pauli_measurement(pauli)
        self.assertEqual(circuit, measurement)
        self.assertEqual(circuit.metadata, {"measured_qubit_indices": meas_indices})


# TODO
@ddt
class TestComposition(TestCase):
    """Test composition logic."""

    def test_compose_single_measurement(self):
        """Test coposition of single base circuit and measurement pair."""


# TODO
@ddt
class TestCalculations(TestCase):
    """Test calculation logic."""


# TODO
@ddt
class TestObservableDecomposer(TestCase):
    """Test ObservableDecomposer strategies."""
