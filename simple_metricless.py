from multiprocessing import set_start_method
try:
    set_start_method('spawn')
except RuntimeError:
    pass
import torch
from torch import nn
from torch.autograd import Variable
from torch.optim import Adam, SGD
from torch.utils.data import DataLoader
from torch.multiprocessing import Pool
from torchvision.datasets import MNIST, FashionMNIST
from torchvision import transforms
from sklearn.model_selection import train_test_split
import seaborn as sns
import numpy as np
import matplotlib.pyplot as plt
import scipy
from algorithms.exp_norm_mixture_fit import fit as fit_exp_norm
from algorithms.digamma_mixture_fit import fit as fit_digamma

from models.MNIST_1h_flexible import MNIST_1h_flexible
from models.MNIST_1h_flexible_sorted import MNIST_1h_flexible_sorted
from models.MNIST_1h_flexible_scaled import MNIST_1h_flexible_scaled
from models.MNIST_1h import MNIST_1h
from variance_metric import get_activations, train as simple_train


EPOCHS = 15

transform = transforms.Compose([
                       transforms.ToTensor(),
                       transforms.Normalize((0.1307,), (0.3081,))
])

if torch.cuda.device_count() > 0:
    wrap = lambda x: x.cuda(async=True) if torch.is_tensor(x) and x.is_pinned() else x.cuda()
    unwrap = lambda x: x.cpu()
else:
    wrap = lambda x: x
    unwrap = wrap

def train(models, dl, lamb=0.001, epochs=EPOCHS):
    criterion = nn.CrossEntropyLoss()
    optimizers = []
    for model in models:
        normal_params = set(model.parameters())
        normal_params.remove(model.x_0)

        optimizer = Adam([{
            'params': normal_params,
            'weight_decay': 0.001,
        }, {
            'params': [model.x_0],
            'lr': 1,
        }])
        optimizers.append(optimizer)
    for e in range(0, epochs):
        print("Epoch %s" % e)
        for i, (images, labels) in enumerate(dl):
            images = wrap(Variable(images, requires_grad=False))
            labels = wrap(Variable(labels, requires_grad=False))
            for model, optimizer in zip(models, optimizers):
                output = model(images)
                optimizer.zero_grad()
                (criterion(output, labels) + lamb * model.loss()).backward()
                # acc = (output.max(1)[1] == labels).float().mean()
                # def tn(x):
                #     return x.cpu().numpy()[0]
                # a = tn(model.x_0.grad.data)
                # if a != a:
                #     return images, labels
                # print(tn(acc.data), tn(model.x_0.data), tn(model.x_0.grad.data))
                optimizer.step()
                if isinstance(model, MNIST_1h_flexible_scaled):
                    model.reorder()

def get_accuracy(models, loader):
    accs = [0] * len(models)
    for images, labels in loader:
        images = wrap(Variable(images, volatile=True))
        labels = wrap(labels)
        for i, model in enumerate(models):
            predicted = model(images).data
            accs[i] += (predicted.max(1)[1] == labels).float().mean()
    return np.array(accs) / len(loader)

def get_dl(dataset, train=True):
    return DataLoader(
        dataset(
            './datasets/%s/' % dataset.__name__,
            train=train,
            download=True,
            transform=transform),
        batch_size=128,
        pin_memory=torch.cuda.device_count() > 0,
        shuffle=True
    )

def plot_convergence(models, sizes, prefix):
    convergences = np.array([m.x_0.data.cpu().numpy()[0] for m in models])
    plt.figure(figsize=(10, 5))
    plt.plot(sizes, convergences)
    plt.title(prefix +' - Network size after training for different starting sizes')
    plt.xlabel('Number of neurons at the beginning')
    plt.ylabel('Number of neurons at the end')
    plt.tight_layout()
    plt.savefig('./plots/%s_1h_simple_flexible_convergence.png' % prefix)
    plt.close()

def plot_accuracies(accuracies, sizes, prefix):
    plt.figure(figsize=(10, 5))
    plt.plot(sizes, accuracies)
    plt.title(prefix +' - Accuracies for different starting sizes')
    plt.xlabel('Number of neurons at the beginning')
    plt.ylabel('Accuracy after training')
    plt.yscale('log')
    plt.grid(True)
    plt.tight_layout()
    plt.savefig('./plots/%s_1h_simple_flexible_accuracies' % prefix)
    plt.close()

def plot_frontier(powers, data, best_acc, prefix):
    plt.figure(figsize=(10, 5))
    a = plt.gca()
    b = a.twinx()
    valid = data[:, 0] > 0
    data = data[valid, :]
    powers = powers[valid]
    xes = np.arange(0, len(powers)) + 1
    ratios = (data[:, 1] - data[:, 2]) * 100
    ratios2 = (data[:, 1] - best_acc) * 100
    w = 0.35
    a.set_xticks(xes)
    a.set_xticklabels(['1e%s' % x for x in powers] , rotation=70) 
    a.bar(xes - w / 2, ratios, 0.35, label="Loss in accuracy vs flexible of same size", color='C0')
    a.bar(xes + w / 2, ratios2, 0.35,  label="Loss in accuracy vs best model", color='C1')
    a.axhline(y=0, color='k')
    a.grid()
    b.plot(xes, data[:, 0], label="Neuron used", color='C2')
    plt.title(prefix +' - Accuracies for the simple flexible model')
    a.set_xlabel('Network size penalty')
    a.set_ylabel('Loss in accurcy (%)')
    b.set_ylabel('Converged netowrk size')
    b.legend(loc="upper right")
    a.legend(loc="upper left")
    plt.tight_layout()
    plt.savefig('./plots/%s_1h_simple_flexible_frontier.png' % prefix)
    # plt.close()

def get_data(params):
    dl, dl2, w = params
    models = [wrap(MNIST_1h_flexible(500, wrap, k)) for k in [250] for _ in range(7)]
    train(models, dl, w, EPOCHS)
    neurons = np.percentile([m.x_0.data.cpu().numpy()[0] for m in models], 50)
    accuracy = np.percentile(get_accuracy(models, dl2), 50)
    if neurons > 0:
        models2 = [wrap(MNIST_1h(int(neurons))) for _ in range(7)]
        simple_train(models2, dl, EPOCHS)
        accuracy2 = np.percentile(get_accuracy(models2, dl2), 50)
    else:
        accuracy2 = 0
    res = neurons, accuracy, accuracy2
    print(res)
    return res

def benchmark_dataset(ds):
    subpool = Pool(40)
    dl = get_dl(ds, True)
    dl2 = get_dl(ds, False)
    sizes = np.array(range(0, 500, 25))
    models = [wrap(MNIST_1h_flexible(500, wrap, k)) for k in range(0, 500, 25)]
    train(models, dl, 1e-5)
    accuracies = np.array(get_accuracy(models, dl))
    plot_accuracies(accuracies, sizes, ds.__name__)
    plot_convergence(models, sizes, ds.__name__)
    powers = -np.arange(2.5, 8, 0.5)
    weights = 10**powers
    data = np.array([get_data((dl, dl2, x)) for x in weights.tolist()])
    best_model = wrap(MNIST_1h(1000))
    simple_train([best_model], dl, EPOCHS)
    plot_frontier(powers, data, get_accuracy([best_model], dl2)[0], ds.__name__)

if __name__ == '__main__':
    benchmark_dataset(MNIST)
    benchmark_dataset(FashionMNIST)
    pass
